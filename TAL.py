"""
Engine core: board representation, state tracking, move execution, legality filtering,
draw detection (50‑move, repetition, insufficient material), and move generation.

Board Representation:
 8x8 list of single-character piece codes. Uppercase = White, lowercase = Black.
 '-' represents an empty square. Promotion easter-egg knights use 'L'/'l'.

Key Responsibilities:
 - Parse + emit FEN (limited: only board + active + castling + en passant + half/fullmove).
 - Track incremental state (king locations, castling rights, en passant targets, logs).
 - Provide legal move list with pin & check resolution.
 - Detect terminal states (checkmate / stalemate / draws).
 - Support reversible make/undo with full restoration of auxiliary logs.

Design Notes:
 - Move objects are immutable after creation (except optional promotion_choice).
 - Board repetition keyed only by piece placement + side-to-move (castling/en-passant
   omitted intentionally for simplified threefold detection).
 - Castling logic validates squares are not attacked via squareUnderAttack probing.
 - Fifty-move counter counts “half-moves” per FIDE rule (100 threshold = draw).

Performance Considerations:
 - Pins & checks detected once per legality cycle then reused in piece move generation.
 - UndoMove reverses logs in strict LIFO order; any new state added must push/pop its log twin.
"""

# --------------------------- FEN Helpers ---------------------------
def fen_to_board(fen):
    """Parse FEN board-part into 8×8 array of single-char piece codes or '-'.
    Only the first field of the FEN is used here; higher fields handled elsewhere."""
    board_part = fen.split()[0]
    rows = board_part.split('/')
    board = []
    for rank in rows:
        row = []
        for ch in rank:
            if ch.isdigit():
                row.extend(['-'] * int(ch))
            else:
                row.append(ch)
        board.append(row)
    return board


def board_to_fen(board):
    """Serialize internal board array back to the FEN board-part (no side/counters)."""
    fen_rows = []
    for row in board:
        empty = 0
        fen_row = ''
        for ch in row:
            if ch == '-':
                empty += 1
            else:
                if empty:
                    fen_row += str(empty)
                    empty = 0
                fen_row += ch
        if empty:
            fen_row += str(empty)
        fen_rows.append(fen_row)
    return '/'.join(fen_rows)


INITIAL_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class GameState:
    """Mutable container for the current position + auxiliary rule state.
    Public Methods Overview:
      makeMove / undoMove  : forward & backward state transitions.
      getValidMoves        : full legal move list (includes castling/filtering).
      getAllPossibleMoves  : pseudo-legal generator (ignores self-check).
      inCheck / squareUnderAttack : tactical probes.
      insufficientMaterial : minimal material draw evaluator.
    """
    def __init__(self, fen=INITIAL_FEN):

        # ── FEN field normalization (pads missing fields with defaults) ──
        # split FEN into fields and pad to 6 parts: board, active, castling, ep, half, full
        parts = fen.strip().split()
        defaults = ['-', '-', '0', '1']  # castling, ep, halfmove, fullmove
        while len(parts) < 6:
            parts.append(defaults[len(parts)-2])
        fields = parts[:6]
        fen = ' '.join(fields)

        self.board = fen_to_board(fen)
        # update king locations from FEN board
        for r in range(8):
            for c in range(8):
                if self.board[r][c] == 'K':
                    self.white_king_location = (r, c)
                elif self.board[r][c] == 'k':
                    self.black_king_location = (r, c)
        # Move dispatch table; 'l'/'L' (easter-egg promoted knight) reuses knight logic.
        self.moveFunctions = {
            'p': self.getPawnMoves, 'r': self.getRookMoves, 'n': self.getKnightMoves,
            'b': self.getBishopMoves, 'q': self.getQueenMoves, 'k': self.getKingMoves,
            'l': self.getKnightMoves
        }
        # Active side derived from FEN field[1]
        self.white_to_move = (fields[1] == 'w')
        self.move_log = []
        #self.white_king_location = (7, 4)
        #self.black_king_location = (0, 4)
        self.checkmate = False
        self.stalemate = False
        self.in_check = False
        self.pins = []
        self.checks = []
        self.enpassant_possible = ()  # coordinates for the square where en-passant capture is possible
        self.enpassant_possible_log = [self.enpassant_possible]
        self.current_castling_rights = CastleRights(True, True, True, True)
        self.castle_rights_log = [CastleRights(self.current_castling_rights.wks, self.current_castling_rights.bks,
                                               self.current_castling_rights.wqs, self.current_castling_rights.bqs)]
        # New attributes for draw detection:
        self.fifty_move_counter = 0
        self.board_position_counts = {}
        self.fifty_move_log = []      
        self.board_key_log = []
        key = self.getBoardKey()
        self.board_position_counts[key] = 1
        # Easter egg: track coordinates of promoted special knights to adjust rendering if needed.
        self.easter_knights = set()

    def getBoardKey(self):
        """Return simplified repetition key (piece placement + side).
        Note: intentionally excludes castling/en-passant for relaxed repetition heuristic."""
        board_str = "".join(["".join(row) for row in self.board])
        return board_str + ("w" if self.white_to_move else "b")

    def updateFiftyMoveCounter(self, move):
        """Increment or reset the fifty-move counter (reset on pawn move or capture)."""
        # ─── First, save the old fifty‐move value onto the log ───
        self.fifty_move_log.append(self.fifty_move_counter)

        if move.piece_moved.lower() == 'p' or move.is_capture:
            self.fifty_move_counter = 0
        else:
            self.fifty_move_counter += 1

    def updateRepetition(self):
        """Record current position key; used for (approximate) threefold repetition."""
        key = self.getBoardKey()
        # ─── Push this key onto board_key_log so undoMove can revert it ───
        self.board_key_log.append(key)

        if key in self.board_position_counts:
            self.board_position_counts[key] += 1
        else:
            self.board_position_counts[key] = 1

    def checkThreefoldRepetition(self):
        """Return True if any recorded position key occurred ≥ 3 times."""
        for count in self.board_position_counts.values():
            if count >= 3:
                return True
        return False

    def insufficientMaterial(self):
        """Detect theoretical impossibility of checkmate under reduced material cases."""
        # Gather all remaining pieces on the board
        pieces = [p for row in self.board for p in row if p != '-']
        # Filter out the two kings
        non_king = [p for p in pieces if p.upper() != 'K']

        #  If there are no other pieces, it's K vs K
        if not non_king:
            return True

        #  If exactly one knight or one bishop remains, it's K+N vs K or K+B vs K
        if len(non_king) == 1 and non_king[0].upper() in ["N", "B"]:
            return True

        # If exactly two knights remain (of either color), it's K+2N vs K
        if len(non_king) == 2 and all(p.upper() == "N" for p in non_king):
            return True

        return False
    def update_fen(self):
        """Reconstruct full FEN string (used by search layer for hashing/TT)."""
        # board part
        board_part = board_to_fen(self.board)
        # active color
        active = 'w' if self.white_to_move else 'b'
        # castling rights
        cr = ('K' if self.current_castling_rights.wks else '') + \
             ('Q' if self.current_castling_rights.wqs else '') + \
             ('k' if self.current_castling_rights.bks else '') + \
             ('q' if self.current_castling_rights.bqs else '')
        cr = cr if cr else '-'
        # en passant square
        if self.enpassant_possible:
            col = Move.cols_to_files[self.enpassant_possible[1]]
            row = Move.rows_to_ranks[self.enpassant_possible[0]]
            ep = f"{col}{row}"
        else:
            ep = '-'
        # halfmove & fullmove counters
        half = self.fifty_move_counter
        full = len(self.move_log) // 2 + 1
        self.current_fen = f"{board_part} {active} {cr} {ep} {half} {full}"

    # ---------------------- Move Execution ----------------------
    def makeMove(self, move):
        """Apply a Move:
           - Updates board, king loci, special moves (promotion, en passant, castling),
             logs (move, en passant, castling, repetition, fifty-move).
           - Does NOT validate legality (caller must use getValidMoves)."""
        # Piece relocation
        # (this will not work for castling, pawn promotion and en-passant)
        self.board[move.start_row][move.start_col] = '-'
        self.board[move.end_row][move.end_col] = move.piece_moved
        self.move_log.append(move)
        # switch players
        self.white_to_move = not self.white_to_move

        # update king's location if moved
        if move.piece_moved == 'K':  # white king
            self.white_king_location = (move.end_row, move.end_col)
        elif move.piece_moved == 'k':  # black king
            self.black_king_location = (move.end_row, move.end_col)

        # ── If the *promoted* knight was captured, drop its marker ──
        if (move.end_row, move.end_col) in self.easter_knights:
            self.easter_knights.remove((move.end_row, move.end_col))

        # pawn promotion
        # if move.is_pawn_promotion:
        #     promo = getattr(move, "promotion_choice", "Q")
        #     # uppercase for white, lowercase for black
        #     promoted = promo.upper() if move.piece_moved.isupper() else promo.lower()
        #     self.board[move.end_row][move.end_col] = promoted
        #     # ── Easter egg: mark new knight promotions ──
        #     if promo.upper() == "N":
        #         self.easter_knights.add((move.end_row, move.end_col))
        if move.is_pawn_promotion:
            promo = getattr(move, "promotion_choice", "Q")
            if promo.upper() == "N":
                # give a unique code to our Easter-egg knight
                promoted = 'L' if move.piece_moved.isupper() else 'l'
            else:
                promoted = promo.upper() if move.piece_moved.isupper() else promo.lower()
            self.board[move.end_row][move.end_col] = promoted

        # enpassant move
        if move.is_enpassant_move:
            self.board[move.start_row][move.end_col] = '-'

        # update enpassant_possible variable
        if move.piece_moved.lower() == 'p' and abs(move.start_row - move.end_row) == 2:
            self.enpassant_possible = ((move.start_row + move.end_row) // 2, move.start_col)
        else:
            self.enpassant_possible = ()

        # castle move
        # if move.is_castle_move:
        #     if move.end_col - move.start_col == 2:  # king-side castle move
        #         self.board[move.end_row][move.end_col - 1] = self.board[move.end_row][
        #             move.end_col + 1]  # moves the rook to its new square
        #         self.board[move.end_row][move.end_col + 1] = '-'  # erase old rook
        #     else:  # queen-side castle move
        #         self.board[move.end_row][move.end_col + 1] = self.board[move.end_row][
        #             move.end_col - 2]  # moves the rook to its new square
        #         self.board[move.end_row][move.end_col - 2] = '-'  # erase old rook
        if move.is_castle_move:
            # If for some reason a non-King got flagged as “castle,” bail out:
            if move.piece_moved not in ('K', 'k'):
                # This should never happen if you only tagged genuine king moves as castles.
                print("WARNING: makeMove got a non-king move with is_castle_move=True:", 
                    move.piece_moved, 
                    f"start=({move.start_row},{move.start_col}), end=({move.end_row},{move.end_col})")
                # We simply skip the “slide rook” bits, so we won’t do an out-of-bounds write.
                return

            dr = move.end_col - move.start_col
            # True kingside castle is “king moved two files to the right”:
            if dr == 2:
                src_rook_col = move.end_col + 1   # e.g. if king ended on g1 (col=6), rook was on h1 (col=7)
                dst_rook_col = move.end_col - 1   # so rook goes to f1 (col=5)
            # True queenside castle is “king moved two files to the left”:
            elif dr == -2:
                src_rook_col = move.end_col - 2   # e.g. if king ended on c1 (col=2), rook was on a1 (col=0)
                dst_rook_col = move.end_col + 1   # so rook goes to d1 (col=3)
            else:
                # This is not a legitimate 2-square king move, so bail out.
                print("WARNING: is_castle_move=True but king did not move ±2 files:", 
                    f"start=({move.start_row},{move.start_col}), end=({move.end_row},{move.end_col})")
                return

            r = move.end_row
            # Sanity check: ensure both src and dst are within 0..7
            if not (0 <= r < 8 and 0 <= src_rook_col < 8 and 0 <= dst_rook_col < 8):
                print("ERROR in makeMove: computed rook indices are out of 0..7!", 
                    "row=", r, 
                    "src_rook_col=", src_rook_col, 
                    "dst_rook_col=", dst_rook_col)
                return

            # Finally, “slide” the rook:
            self.board[r][dst_rook_col] = self.board[r][src_rook_col]
            self.board[r][src_rook_col] = '-'

        # Track moved promoted knights for optional custom rendering.
        if move.piece_moved.upper() == 'N' and (move.start_row, move.start_col) in self.easter_knights:
            self.easter_knights.remove((move.start_row, move.start_col))
            self.easter_knights.add((move.end_row, move.end_col))

        self.enpassant_possible_log.append(self.enpassant_possible)

        # update castling rights - whenever it is a rook or king move
        # self.updateCastleRights(move)
        # self.castle_rights_log.append(CastleRights(self.current_castling_rights.wks, self.current_castling_rights.bks,
        #                                            self.current_castling_rights.wqs, self.current_castling_rights.bqs))
        self.castle_rights_log.append(
            CastleRights(
                self.current_castling_rights.wks,
                self.current_castling_rights.bks,
                self.current_castling_rights.wqs,
                self.current_castling_rights.bqs
            )
        )

        # New: update fifty move counter and board position count
        self.updateFiftyMoveCounter(move)
        self.updateRepetition()
        self.update_fen()  # rename/update FEN

    def undoMove(self):
        """Reverse last move; restores:
           - board & king positions
           - repetition counts
           - fifty-move counter
           - en passant target
           - castling rights
           - rook movement from castling
        Safe no-op if move_log empty."""
        if not self.move_log:
            return

        # ─── Revert repetition count (threefold) ───
        if self.board_key_log:
            last_key = self.board_key_log.pop()
            cnt = self.board_position_counts.get(last_key, 0)
            if cnt <= 1:
                # remove entirely if it was only 1
                self.board_position_counts.pop(last_key, None)
            else:
                self.board_position_counts[last_key] = cnt - 1

        # ─── Revert fifty‐move counter ───
        if self.fifty_move_log:
            prev_fifty = self.fifty_move_log.pop()
            self.fifty_move_counter = prev_fifty

        move = self.move_log.pop()

        #  Move the king (or other piece) back to its start square
        self.board[move.start_row][move.start_col] = move.piece_moved
        #  Restore what was on the destination square (captured piece or "-")
        self.board[move.end_row][move.end_col]     = move.piece_captured if move.piece_captured else '-'

        # Swap side to move back
        self.white_to_move = not self.white_to_move

        # Update king location if necessary
        if move.piece_moved == 'K':  # white king
            self.white_king_location = (move.start_row, move.start_col)
        elif move.piece_moved == 'k':  # black king
            self.black_king_location = (move.start_row, move.start_col)

        # Undo en passant if it was an en passant capture
        if move.is_enpassant_move:
            # On an en passant capture, the captured pawn sat “behind” the landing square.
            self.board[move.end_row][move.end_col] = '-'  # landing square was empty
            self.board[move.start_row][move.end_col] = move.piece_captured

        # Restore the previous en passant‐possible square
        self.enpassant_possible_log.pop()
        self.enpassant_possible = self.enpassant_possible_log[-1]

        # Undo castling rights
        self.castle_rights_log.pop()
        self.current_castling_rights = self.castle_rights_log[-1]

        #  ONLY undo a rook‐slide if this was truly a two‐square king castle:
        if move.is_castle_move and move.piece_moved in ('K', 'k'):
            dr = move.end_col - move.start_col
            r  = move.end_row

            # Was it a genuine kingside castle (king moved +2 files)?
            if dr == 2:
                # During makeMove, the rook moved from (r, end_col+1) to (r, end_col−1).
                src = move.end_col - 1  # where the rook currently sits
                dst = move.end_col + 1  # where it needs to go back
            # Was it a genuine queenside castle (king moved −2 files)?
            elif dr == -2:
                # During makeMove, the rook moved from (r, end_col−2) to (r, end_col+1).
                src = move.end_col + 1
                dst = move.end_col - 2
            else:
                # is_castle_move=True, but dr wasn’t ±2 → skip the rook slide
                print("WARNING in undoMove: is_castle_move=True but king did not move ±2 files:",
                      f"start=({move.start_row},{move.start_col}), end=({move.end_row},{move.end_col})")
                src = dst = None

            # Only do the slide if indices are valid
            if src is not None and 0 <= r < 8 and 0 <= src < 8 and 0 <= dst < 8:
                self.board[r][dst] = self.board[r][src]
                self.board[r][src] = '-'
            elif src is not None:
                # We expected to undo a real castle, but something is out of bounds
                print(" ERROR in undoMove: computed rook indices out of 0..7!",
                      f"row={r}, src={src}, dst={dst}")
        # Reset checkmate/stalemate flags and update FEN
        self.checkmate = False
        self.stalemate = False
        self.update_fen()

    def updateCastleRights(self, move):
        """Adjust castling rights only when a rook/king moves or rook is captured on its home square."""
        # White’s queen‐side: only if a White rook on a1 was captured
        if move.piece_captured == 'R' and move.end_row == 7 and move.end_col == 0:
            self.current_castling_rights.wqs = False
        # White’s king‐side: only if a White rook on h1 was captured
        if move.piece_captured == 'R' and move.end_row == 7 and move.end_col == 7:
            self.current_castling_rights.wks = False

        # Black’s queen‐side: only if a Black rook on a8 was captured
        if move.piece_captured == 'r' and move.end_row == 0 and move.end_col == 0:
            self.current_castling_rights.bqs = False
        # Black’s king‐side: only if a Black rook on h8 was captured
        if move.piece_captured == 'r' and move.end_row == 0 and move.end_col == 7:
            self.current_castling_rights.bks = False

        # If the white king itself moved, lose both White rights immediately:
        if move.piece_moved == 'K':
            self.current_castling_rights.wqs = False
            self.current_castling_rights.wks = False
        # If the black king moved, lose both Black rights:
        elif move.piece_moved == 'k':
            self.current_castling_rights.bqs = False
            self.current_castling_rights.bks = False

        # If a White rook moved off its original square, lose that side’s right
        if move.piece_moved == 'R':
            # White rook on a1 → queen‐side loss
            if move.start_row == 7 and move.start_col == 0:
                self.current_castling_rights.wqs = False
            # White rook on h1 → king‐side loss
            elif move.start_row == 7 and move.start_col == 7:
                self.current_castling_rights.wks = False

        # If a Black rook moved off its original square, lose that side’s right
        if move.piece_moved == 'r':
            # Black rook on a8 → queen‐side loss
            if move.start_row == 0 and move.start_col == 0:
                self.current_castling_rights.bqs = False
            # Black rook on h8 → king‐side loss
            elif move.start_row == 0 and move.start_col == 7:
                self.current_castling_rights.bks = False

    # ---------------------- Move Generation (Legal) ----------------------
    def getValidMoves(self):
        """Return list of legal Move objects after:
           - generating pseudo moves
           - filtering via check / pin logic
           - adding castling if legal
           - enforcing draws (mate/stalemate flags set)."""
        temp_castle_rights = CastleRights(self.current_castling_rights.wks, self.current_castling_rights.bks,
                                          self.current_castling_rights.wqs, self.current_castling_rights.bqs)
        # advanced algorithm
        moves = []
        self.in_check, self.pins, self.checks = self.checkForPinsAndChecks()

        if self.white_to_move:
            king_row = self.white_king_location[0]
            king_col = self.white_king_location[1]
        else:
            king_row = self.black_king_location[0]
            king_col = self.black_king_location[1]
        if self.in_check:
            if len(self.checks) == 1:  # only 1 check, block the check or move the king
                moves = self.getAllPossibleMoves()
                # to block the check you must put a piece into one of the squares between the enemy piece and your king
                check = self.checks[0]  # check information
                check_row, check_col = check[0], check[1]
                piece_checking = self.board[check_row][check_col]
                valid_squares = []
                # knight check if the checking piece is a knight
                if piece_checking.upper() == "N":
                    valid_squares = [(check_row, check_col)]
                else:
                    for i in range(1, 8):
                        valid_square = (king_row + check[2] * i,
                                        king_col + check[3] * i)  # check[2] and check[3] are the check directions
                        valid_squares.append(valid_square)
                        if valid_square[0] == check_row and valid_square[
                            1] == check_col:  # once you get to piece and check
                            break
                # filter out non-king moves that don't block/capture
                for i in range(len(moves) - 1, -1, -1):
                    # skip king moves, others must land in valid_squares
                    if moves[i].piece_moved.upper() != "K" and (moves[i].end_row, moves[i].end_col) not in valid_squares:
                        moves.remove(moves[i])
            else:  # double check, king has to move
                self.getKingMoves(king_row, king_col, moves)
        else:  # not in check - all moves are fine
            moves = self.getAllPossibleMoves()
            if self.white_to_move:
                self.getCastleMoves(self.white_king_location[0], self.white_king_location[1], moves)
            else:
                self.getCastleMoves(self.black_king_location[0], self.black_king_location[1], moves)

        # filter out any move that would capture a king
        moves = [m for m in moves if m.piece_captured.upper() != 'K']

        if len(moves) == 0:
            if self.inCheck():
                self.checkmate = True
            else:
                self.stalemate = True
            # No legal moves + (checkmate or stalemate). Return immediately.
            self.current_castling_rights = temp_castle_rights
            return []

        # There _are_ legal moves: reset checkmate/stalemate for now
        self.checkmate = False
        self.stalemate = False

        #  Draw by insufficient material?
        if self.insufficientMaterial():
            self.stalemate = True
            self.current_castling_rights = temp_castle_rights
            return []

        #  Draw by three‐fold repetition?
        if self.checkThreefoldRepetition():
            self.stalemate = True
            self.current_castling_rights = temp_castle_rights
            return []

        #  Draw by 50‐move rule (100 half moves without pawn move or capture)?
        if self.fifty_move_counter >= 100:
            self.stalemate = True
            self.current_castling_rights = temp_castle_rights
            return []

        # None of the draw conditions held ⇒ return the full list of legal moves.
        self.current_castling_rights = temp_castle_rights
        return moves
        #     if self.inCheck():
        #         self.checkmate = True
        #     else:
        #         self.stalemate = True
        # else:
        #     self.checkmate = False
        #     self.stalemate = False

        # self.current_castling_rights = temp_castle_rights
        # # Insufficient material, threefold repetition or 50-move rule draw.
        # if self.insufficientMaterial() or self.checkThreefoldRepetition() or self.fifty_move_counter >= 100:
        #     self.stalemate = True
        # return moves

    def inCheck(self):
        """Return True if side-to-move's king is currently attacked."""
        if self.white_to_move:
            return self.squareUnderAttack(self.white_king_location[0], self.white_king_location[1])
        else:
            return self.squareUnderAttack(self.black_king_location[0], self.black_king_location[1])

    def squareUnderAttack(self, row, col):
        """Probe if (row,col) is attacked by flipping side and scanning their pseudo moves."""
        self.white_to_move = not self.white_to_move  # switch to opponent's point of view
        opponents_moves = self.getAllPossibleMoves()
        self.white_to_move = not self.white_to_move
        for move in opponents_moves:
            if move.end_row == row and move.end_col == col:  # square is under attack
                return True
        return False

    # ---------------------- Move Generation (Pseudo) ----------------------
    def getAllPossibleMoves(self):
        """Generate pseudo-legal moves (no self-check filtering)."""
        moves = []
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece != '-' and ((piece.isupper() and self.white_to_move)
                                     or (piece.islower() and not self.white_to_move)):
                    key = piece.lower()
                    self.moveFunctions[key](r, c, moves)
        return moves

    def checkForPinsAndChecks(self):
        """Scan outward from king to classify:
           - pins (ally pieces that cannot move off ray)
           - checks (enemy pieces directly attacking king)
        Also detects knight attacks separately."""
        pins = []  # squares pinned and the direction its pinned from
        checks = []  # squares where enemy is applying a check
        in_check = False
        if self.white_to_move:
            enemy_color = "b"
            ally_color = "w"
            start_row = self.white_king_location[0]
            start_col = self.white_king_location[1]
        else:
            enemy_color = "w"
            ally_color = "b"
            start_row = self.black_king_location[0]
            start_col = self.black_king_location[1]
        # check outwards from king for pins and checks, keep track of pins
        directions = ((-1, 0), (0, -1), (1, 0), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
        for j, direction in enumerate(directions):
            possible_pin = ()
            for i in range(1, 8):
                end_row = start_row + direction[0] * i
                end_col = start_col + direction[1] * i
                if 0 <= end_row <= 7 and 0 <= end_col <= 7:
                    end_piece = self.board[end_row][end_col]
                    if end_piece != '-' and ((end_piece.isupper() and ally_color == 'w') or
                                             (end_piece.islower() and ally_color == 'b')) \
                       and end_piece.upper() != 'K':
                        if possible_pin == ():
                            possible_pin = (end_row, end_col, direction[0], direction[1])
                        else:
                            break
                    elif end_piece != '-' and ((end_piece.isupper() and enemy_color == 'w') or
                                               (end_piece.islower() and enemy_color == 'b')):
                        enemy_type = end_piece.upper()
                        # 5 possibilities in this complex conditional
                        # 1.) orthogonally away from king and piece is a rook
                        # 2.) diagonally away from king and piece is a bishop
                        # 3.) 1 square away diagonally from king and piece is a pawn
                        # 4.) any direction and piece is a queen
                        # 5.) any direction 1 square away and piece is a king
                        if (0 <= j <= 3 and enemy_type == "R") or (4 <= j <= 7 and enemy_type == "B") or (
                                i == 1 and enemy_type == "P" and (
                                (enemy_color == "w" and 6 <= j <= 7) or (enemy_color == "b" and 4 <= j <= 5))) or (
                                enemy_type == "Q") or (i == 1 and enemy_type == "K"):
                            if possible_pin == ():  # no piece blocking, so check
                                in_check = True
                                checks.append((end_row, end_col, direction[0], direction[1]))
                                break
                            else:  # piece blocking so pin
                                pins.append(possible_pin)
                                break
                        else:  # enemy piece not applying checks
                            break
                else:
                    break  # off board
        # check for knight checks
        knight_moves = ((-2, -1), (-2, 1), (-1, 2), (1, 2), (2, -1), (2, 1), (-1, -2), (1, -2))
        for move in knight_moves:
            end_row = start_row + move[0]
            end_col = start_col + move[1]
            if 0 <= end_row <= 7 and 0 <= end_col <= 7:
                end_piece = self.board[end_row][end_col]
                if end_piece != '-' and ((end_piece.isupper() and enemy_color == 'w') or
                                         (end_piece.islower() and enemy_color == 'b')) \
                   and end_piece.upper() == 'N':
                    in_check = True
                    checks.append((end_row, end_col, move[0], move[1]))
        return in_check, pins, checks

    # Piece-type specific generators below; each respects current pin context.
    def getPawnMoves(self, row, col, moves):
        """Add legal pawn advances, captures, en passant (with discovered-check safety)."""
        piece_pinned = False
        pin_direction = ()
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                pin_direction = (self.pins[i][2], self.pins[i][3])
                self.pins.remove(self.pins[i])
                break

        if self.white_to_move:
            move_amount = -1
            start_row = 6
            enemy_color = "b"
            king_row, king_col = self.white_king_location
        else:
            move_amount = 1
            start_row = 1
            enemy_color = "w"
            king_row, king_col = self.black_king_location

        if self.board[row + move_amount][col] == "-":  # 1 square pawn advance
            if not piece_pinned or pin_direction == (move_amount, 0):
                moves.append(Move((row, col), (row + move_amount, col), self.board))
                if row == start_row and self.board[row + 2 * move_amount][col] == "-":  # 2 square pawn advance
                    moves.append(Move((row, col), (row + 2 * move_amount, col), self.board))

        # capture‐left
        if col - 1 >= 0:
            if not piece_pinned or pin_direction == (move_amount, -1):
                end_piece = self.board[row + move_amount][col - 1]
                if end_piece != '-' and ((end_piece.islower() and self.white_to_move)
                                        or (end_piece.isupper() and not self.white_to_move)):
                    moves.append(Move((row, col), (row + move_amount, col - 1), self.board))
            # en-passant to the left
            if (row + move_amount, col - 1) == self.enpassant_possible:
                attacking_piece = blocking_piece = False
                if king_row == row:
                    # define the squares between king and pawn (inside) and beyond pawn (outside)
                    if king_col < col:
                        inside_range  = range(king_col + 1, col - 1)
                        outside_range = range(col + 1, 8)
                    else:
                        inside_range  = range(king_col - 1, col, -1)
                        outside_range = range(col - 2, -1, -1)
                    # any blocker between king and pawn?
                    for i in inside_range:
                        if self.board[row][i] != "-":
                            blocking_piece = True
                    # look for an enemy rook/queen beyond pawn
                    for i in outside_range:
                        sq = self.board[row][i]
                        if sq != "-":
                            if ((sq.isupper() and enemy_color == 'w') or
                                (sq.islower() and enemy_color == 'b')) \
                            and sq.upper() in ('R','Q'):
                                attacking_piece = True
                            else:
                                blocking_piece = True
                if not attacking_piece or blocking_piece:
                    moves.append(Move((row, col),
                                    (row + move_amount, col - 1),
                                    self.board,
                                    is_enpassant_move=True))

        # capture‐right
        if col + 1 <= 7:
            if not piece_pinned or pin_direction == (move_amount, 1):
                end_piece = self.board[row + move_amount][col + 1]
                if end_piece != '-' and ((end_piece.islower() and self.white_to_move)
                                        or (end_piece.isupper() and not self.white_to_move)):
                    moves.append(Move((row, col), (row + move_amount, col + 1), self.board))
            # en-passant to the right
            if (row + move_amount, col + 1) == self.enpassant_possible:
                attacking_piece = blocking_piece = False
                if king_row == row:
                    # define inside and outside ranges for right‐side check
                    if king_col < col:
                        inside_range  = range(king_col + 1, col)
                        outside_range = range(col + 2, 8)
                    else:
                        inside_range  = range(king_col - 1, col + 1, -1)
                        outside_range = range(col - 1, -1, -1)
                    for i in inside_range:
                        if self.board[row][i] != "-":
                            blocking_piece = True
                    for i in outside_range:
                        sq = self.board[row][i]
                        if sq != "-":
                            if ((sq.isupper() and enemy_color == 'w') or
                                (sq.islower() and enemy_color == 'b')) \
                            and sq.upper() in ('R','Q'):
                                attacking_piece = True
                            else:
                                blocking_piece = True
                if not attacking_piece or blocking_piece:
                    moves.append(Move((row, col),
                                    (row + move_amount, col + 1),
                                    self.board,
                                    is_enpassant_move=True))

    def getRookMoves(self, row, col, moves):
        """Sliding horizontal/vertical rook moves with pin respect."""
        piece_pinned = False
        pin_direction = ()
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                pin_direction = (self.pins[i][2], self.pins[i][3])
                #if self.board[row][col][
                #   1] != "Q":  # can't remove queen from pin on rook moves, only remove it on bishop moves
                # only remove the pin here if the pinned piece isn't a queen
                piece = self.board[row][col]
                if piece.upper() != "Q":
                    self.pins.remove(self.pins[i])
                break

        directions = ((-1, 0), (0, -1), (1, 0), (0, 1))  # up, left, down, right
        enemy_color = "b" if self.white_to_move else "w"
        for direction in directions:
            for i in range(1, 8):
                end_row = row + direction[0] * i
                end_col = col + direction[1] * i
                if 0 <= end_row <= 7 and 0 <= end_col <= 7:  # check for possible moves only in boundaries of the board
                    if not piece_pinned or pin_direction == direction or pin_direction == (
                            -direction[0], -direction[1]):
                        end_piece = self.board[end_row][end_col]
                        if end_piece == "-":  # empty space is valid
                            moves.append(Move((row, col), (end_row, end_col), self.board))
                        elif (end_piece.isupper() if enemy_color == 'w' else end_piece.islower()):  # capture enemy piece
                            moves.append(Move((row, col), (end_row, end_col), self.board))
                            break
                        else:  # friendly piece
                            break
                else:  # off board
                    break

    def getKnightMoves(self, row, col, moves):
        """Knight L-moves (ignore pins except removal bookkeeping)."""
        piece_pinned = False
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                self.pins.remove(self.pins[i])
                break

        knight_moves = ((-2, -1), (-2, 1), (-1, 2), (1, 2), (2, -1), (2, 1), (-1, -2),
                        (1, -2))  # up/left up/right right/up right/down down/left down/right left/up left/down
        ally_color = "w" if self.white_to_move else "b"
        for offset in knight_moves:
            end_row = row + offset[0]
            end_col = col + offset[1]
            if 0 <= end_row <= 7 and 0 <= end_col <= 7:
                if not piece_pinned:
                    end_piece = self.board[end_row][end_col]
                    # allow move only to empty or opponent-occupied square
                    if end_piece == '-' or ((end_piece.isupper() and not self.white_to_move)
                                             or (end_piece.islower() and self.white_to_move)):
                        moves.append(Move((row, col), (end_row, end_col), self.board))

    def getBishopMoves(self, row, col, moves):
        """Diagonal sliding bishop moves with pin respect."""
        piece_pinned = False
        pin_direction = ()
        for i in range(len(self.pins) - 1, -1, -1):
            if self.pins[i][0] == row and self.pins[i][1] == col:
                piece_pinned = True
                pin_direction = (self.pins[i][2], self.pins[i][3])
                self.pins.remove(self.pins[i])
                break

        directions = ((-1, -1), (-1, 1), (1, 1), (1, -1))  # diagonals: up/left up/right down/right down/left
        enemy_color = "b" if self.white_to_move else "w"
        for direction in directions:
            for i in range(1, 8):
                end_row = row + direction[0] * i
                end_col = col + direction[1] * i
                if 0 <= end_row <= 7 and 0 <= end_col <= 7:  # check if the move is on board
                    if not piece_pinned or pin_direction == direction or pin_direction == (
                            -direction[0], -direction[1]):
                        end_piece = self.board[end_row][end_col]
                        if end_piece == "-":  # empty space is valid
                            moves.append(Move((row, col), (end_row, end_col), self.board))
                        elif (end_piece.isupper() if enemy_color == 'w' else end_piece.islower()):  # capture enemy piece
                            moves.append(Move((row, col), (end_row, end_col), self.board))
                            break
                        else:  # friendly piece
                            break
                else:  # off board
                    break

    def getQueenMoves(self, row, col, moves):
        """Queen = bishop + rook moves (delegates)."""
        self.getBishopMoves(row, col, moves)
        self.getRookMoves(row, col, moves)

    def getKingMoves(self, row, col, moves):
        """Single-step king moves; temporarily relocates king to test exposure."""
        row_moves = (-1, -1, -1, 0, 0, 1, 1, 1)
        col_moves = (-1, 0, 1, -1, 1, -1, 0, 1)
        ally_color = "w" if self.white_to_move else "b"
        for i in range(8):
            end_row = row + row_moves[i]
            end_col = col + col_moves[i]
            if 0 <= end_row <= 7 and 0 <= end_col <= 7:
                end_piece = self.board[end_row][end_col]
                # allow move only to empty or opponent-occupied square
                if end_piece == '-' or ((end_piece.isupper() and not self.white_to_move)
                                         or (end_piece.islower() and self.white_to_move)):
                    # place king on end square and check for checks
                    if ally_color == "w":
                        self.white_king_location = (end_row, end_col)
                    else:
                        self.black_king_location = (end_row, end_col)
                    in_check, pins, checks = self.checkForPinsAndChecks()
                    if not in_check:
                        moves.append(Move((row, col), (end_row, end_col), self.board))
                    # place king back on original location
                    if ally_color == "w":
                        self.white_king_location = (row, col)
                    else:
                        self.black_king_location = (row, col)

    def getCastleMoves(self, row, col, moves):
        """Wrapper: conditionally add king/queen side castle if rights & path safe."""
        if self.squareUnderAttack(row, col):
            return  # can't castle while in check
        if (self.white_to_move and self.current_castling_rights.wks) or (
                not self.white_to_move and self.current_castling_rights.bks):
            self.getKingsideCastleMoves(row, col, moves)
        if (self.white_to_move and self.current_castling_rights.wqs) or (
                not self.white_to_move and self.current_castling_rights.bqs):
            self.getQueensideCastleMoves(row, col, moves)

    def getKingsideCastleMoves(self, row, col, moves):
        """Validate kingside castle preconditions (rook presence, empties, attack-free path)."""
        # (A) Determine which color we're talking about:
        if self.white_to_move:
            # White’s home‐rank is 7; home‐file is 4 (e1).
            if row != 7 or col != 4:
                return
            # Must have the rook on h1:
            if self.board[7][7] != 'R':
                return
            # Must still have White king‐side rights:
            if not self.current_castling_rights.wks:
                return
            # Squares between e1–h1 must be empty:
            if self.board[7][5] != '-' or self.board[7][6] != '-':
                return
            # King cannot be in check on e1, nor can f1/g1 be under attack:
            if self.squareUnderAttack(7, 4) or self.squareUnderAttack(7, 5) or self.squareUnderAttack(7, 6):
                return
            # All conditions passed; add the castle move:
            moves.append(Move((7, 4), (7, 6), self.board, is_castle_move=True))
        else:
            # Black’s home‐rank is 0; home‐file is 4 (e8).
            if row != 0 or col != 4:
                return
            # Must have the rook on h8:
            if self.board[0][7] != 'r':
                return
            # Must still have Black king‐side rights:
            if not self.current_castling_rights.bks:
                return
            # Squares between e8–h8 must be empty:
            if self.board[0][5] != '-' or self.board[0][6] != '-':
                return
            # King cannot be in check on e8, nor can f8/g8 be under attack:
            if self.squareUnderAttack(0, 4) or self.squareUnderAttack(0, 5) or self.squareUnderAttack(0, 6):
                return
            # All conditions passed; add the castle move:
            moves.append(Move((0, 4), (0, 6), self.board, is_castle_move=True))


    def getQueensideCastleMoves(self, row, col, moves):
        """Validate queenside castle preconditions (rook presence, empties, attack-free path)."""
        if self.white_to_move:
            # White’s home‐rank is 7; home‐file is 4 (e1).
            if row != 7 or col != 4:
                return
            # Must have the rook on a1:
            if self.board[7][0] != 'R':
                return
            # Must still have White queen‐side rights:
            if not self.current_castling_rights.wqs:
                return
            # Squares between a1–e1 are b1 (col=1), c1 (col=2), d1 (col=3):
            if self.board[7][1] != '-' or self.board[7][2] != '-' or self.board[7][3] != '-':
                return
            # King cannot be in check on e1, nor can d1/c1 be under attack:
            if self.squareUnderAttack(7, 4) or self.squareUnderAttack(7, 3) or self.squareUnderAttack(7, 2):
                return
            # All conditions passed; add the castle move:
            moves.append(Move((7, 4), (7, 2), self.board, is_castle_move=True))
        else:
            # Black’s home‐rank is 0; home‐file is 4 (e8).
            if row != 0 or col != 4:
                return
            # Must have the rook on a8:
            if self.board[0][0] != 'r':
                return
            # Must still have Black queen‐side rights:
            if not self.current_castling_rights.bqs:
                return
            # Squares between a8–e8 are b8(1), c8(2), d8(3):
            if self.board[0][1] != '-' or self.board[0][2] != '-' or self.board[0][3] != '-':
                return
            # King cannot be in check on e8, nor can d8/c8 be under attack:
            if self.squareUnderAttack(0, 4) or self.squareUnderAttack(0, 3) or self.squareUnderAttack(0, 2):
                return
            # All conditions passed; add the castle move:
            moves.append(Move((0, 4), (0, 2), self.board, is_castle_move=True))


class CastleRights:
    """Lightweight struct for current available castling sides."""
    def __init__(self, wks, bks, wqs, bqs):
        self.wks = wks
        self.bks = bks
        self.wqs = wqs
        self.bqs = bqs


class Move:
    """Immutable representation of a move (except optional promotion_choice later).
    Includes flags for:
      - pawn promotion
      - en passant
      - castling
      - capture
    moveID used for quick equality (compact encoding of coordinates)."""
    # Mapping dictionaries (rank/file conversion).
    ranks_to_rows = {"1": 7, "2": 6, "3": 5, "4": 4,
                     "5": 3, "6": 2, "7": 1, "8": 0}
    rows_to_ranks = {v: k for k, v in ranks_to_rows.items()}
    files_to_cols = {"a": 0, "b": 1, "c": 2, "d": 3,
                     "e": 4, "f": 5, "g": 6, "h": 7}
    cols_to_files = {v: k for k, v in files_to_cols.items()}

    def __init__(self, start_square, end_square, board, is_enpassant_move=False, is_castle_move=False):
        # Derive moved/captured pieces + classify special move flags.
        self.start_row = start_square[0]
        self.start_col = start_square[1]
        self.end_row = end_square[0]
        self.end_col = end_square[1]
        self.piece_moved = board[self.start_row][self.start_col]
        self.piece_captured = board[self.end_row][self.end_col]
        # pawn promotion if single-char 'P'/'p' to last rank
        self.is_pawn_promotion = (self.piece_moved.lower() == 'p' and
                                  (self.end_row == 0 or self.end_row == 7))
        # en passant
        self.is_enpassant_move = is_enpassant_move
        if self.is_enpassant_move:
            self.piece_captured = 'p' if self.piece_moved.upper() == 'P' else 'P'
        # castle
        self.is_castle_move = is_castle_move
        self.is_capture = (self.piece_captured != '-')
        self.moveID = self.start_row * 1000 + self.start_col * 100 + self.end_row * 10 + self.end_col

    def __eq__(self, other):
        """Equality: same encoded moveID."""
        if isinstance(other, Move):
            return self.moveID == other.moveID
        return False

    def getChessNotation(self):
        """Return simplified algebraic-ish string (no disambiguation, minimal SAN subset)."""
        if self.is_pawn_promotion:
            return self.getRankFile(self.end_row, self.end_col) + "Q"
        if self.is_castle_move:
            # long castle if king ends on file 'c' (col=2)
            if self.end_col == 2:
                return "0-0-0"
            # otherwise short castle
            return "0-0"
        if self.is_enpassant_move:
            return (self.getRankFile(self.start_row, self.start_col)[0] + "x" +
                    self.getRankFile(self.end_row, self.end_col) + " e.p.")
        # capture
        if self.piece_captured != "-":
            if self.piece_moved.lower() == "p":
                return (self.getRankFile(self.start_row, self.start_col)[0] + "x" +
                        self.getRankFile(self.end_row, self.end_col))
            return self.piece_moved.upper() + "x" + self.getRankFile(self.end_row, self.end_col)
        # non-capture
        if self.piece_moved.lower() == "p":
            return self.getRankFile(self.end_row, self.end_col)
        return self.piece_moved.upper() + self.getRankFile(self.end_row, self.end_col)

    def getRankFile(self, row, col):
        """Convert (row,col) → algebraic coordinate like 'e4'."""
        return self.cols_to_files[col] + self.rows_to_ranks[row]

    def __str__(self):
        """Readable form with capture/promotion/castle shorthand."""
        if self.is_castle_move:
            return "0-0" if self.end_col == 6 else "0-0-0"
        end_square = self.getRankFile(self.end_row, self.end_col)
        # pawn
        if self.piece_moved.lower() == "p":
            if self.is_capture:
                return self.cols_to_files[self.start_col] + "x" + end_square
            return end_square + ("Q" if self.is_pawn_promotion else "")
        # other pieces
        move_string = self.piece_moved.upper()
        if self.is_capture:
            move_string += "x"
        return move_string + end_square
