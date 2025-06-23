import random
import math
from collections import defaultdict
from TAL import GameState

# Material scores
piece_score = {"P": 100, "N": 300, "B": 320, "R": 500, "Q": 900, "K": 0, "L": 300}

# Piece-square tables
pawn_table = [[0, 0, 0, 0, 0, 0, 0, 0],
                [50, 50, 50, 50, 50, 50, 50, 50],
                [10, 10, 20, 30, 30, 20, 10, 10],
                [5, 5, 10, 25, 25, 10, 5, 5],
                [0, 0, 0, 20, 20, 0, 0, 0],
                [5, -5, -10, 0, 0, -10, -5, 5],
                [5, 10, 10, -20, -20, 10, 10, 5],
                [0, 0, 0, 0, 0, 0, 0, 0]]

knight_table = [[-50, -40, -30, -30, -30, -30, -40, -50],
                [-40, -20, 0, 0, 0, 0, -20, -40],
                [-30, 0, 10, 15, 15, 10, 0, -30],
                [-30, 5, 15, 20, 20, 15, 5, -30],
                [-30, 0, 15, 20, 20, 15, 0, -30],
                [-30, 5, 10, 15, 15, 10, 5, -30],
                [-40, -20, 0, 5, 5, 0, -20, -40],
                [-50, -40, -30, -30, -30, -30, -40, -50]]

bishop_table = [[-20, -10, -10, -10, -10, -10, -10, -20],
                [-10, 0, 0, 0, 0, 0, 0, -10],
                [-10, 0, 5, 5, 5, 5, 0, -10],
                [-10, 0, 5, 10, 10, 5, 0, -10],
                [-10, 0, 5, 10, 10, 5, 0, -10],
                [-10, 0, 5, 5, 5, 5, 0, -10],
                [-10, 0, 0, 0, 0, 0, 0, -10],
                [-20, -10, -10, -10, -10, -10, -10, -20]]

rook_table = [[0, 0, 0, 0, 0, 0, 0, 0],
                [20, 20, 20, 20, 20, 20, 20, 10],
                [-10, -5, -5, -5, -5, -5, -5, -10],
                [-10, -5, -5, 0, 0, -5, -5, -10],
                [-10, -5, -5, 0, 0, -5, -5, -10],
                [-10, -5, -5, -5, -5, -5, -5, -10],
                [-10, -5, -5, -5, -5, -5, -5, -10],
                [0, 0, 10, 10, 10, 10, 10, 0]]

queen_table = [[-20, -10, -5, 0, 0, 0, -10, -20],
                [-10, 0, 5, 10, 10, 5, 0, -10],
                [-5, 5, 15, 20, 20, 15, 5, -5],
                [0, 10, 20, 20, 20, 20, 10, 0],
                [0, 10, 20, 20, 20, 20, 10, 0],
                [-5, 5, 15, 20, 20, 15, 5, -5],
                [-10, 0, 5, 10, 10, 5, 0, -10],
                [-20, -10, -5, 0, 0, 0, -10, -20]]

king_table = [[-80, -70, -70, -70, -70, -70, -70, -80],
                [-60, -60, -60, -60, -60, -60, -60, -60],
                [-40, -50, -50, -60, -60, -50, -50, -40],
                [-30, -40, -40, -50, -50, -40, -40, -30],
                [-20, -30, -30, -40, -40, -30, -30, -20],
                [-10, -20, -20, -20, -20, -20, -20, -10],
                [20, 20, -5, -5, -5, -5, 20, 20],
                [20, 30, 10, 0, 0, 10, 30, 20]]

king_endgame_table = [[-20, -10, -10, -10, -10, -10, -10, -20],
                [ -5,   0,   5,   5,   5,   5,   0,  -5],
                [-10,  -5,  20,  30,  30,  20,  -5, -10],
                [-15, -10,  35,  45,  45,  35, -10, -15],
                [-20, -15,  30,  40,  40,  30, -15, -20],
                [-25, -20,  20,  25,  25,  20, -20, -25],
                [-30, -25,   0,   0,   0,   0, -25, -30],
                [-50, -30, -30, -30, -30, -30, -30, -50]
]
# We know there are 32 total pieces at start of the game, of which 2 are kings → 29 non‐king pieces.
MAX_NON_KING_PIECES = 29

_tables = {
    "P": pawn_table, "N": knight_table, "B": bishop_table,
    "R": rook_table, "Q": queen_table, "K": king_table,
    "L": knight_table  
}
CHECKMATE = 100000
STALEMATE = 0

# Transposition table with flags
# each entry: tt[key] = {"value": val, "flag": "EXACT"/"LOWER"/"UPPER"}
_tt = {}

# Killer and history heuristics
_killers = defaultdict(lambda: [None, None])
_history = defaultdict(int)

# tune how strong the “center‐distance” and “king‐proximity” bonuses are:
CENTER_WEIGHT = 15
PROX_WEIGHT   = 7
# Maximum possible Manhattan distance between two kings on an empty 8×8 board:
MAX_KING_MANHATTAN = 14
import random
import math
from collections import defaultdict
from TAL import GameState

def findBestMove(gs, moves, return_queue, depth=4):
    turn_white = gs.white_to_move
    best_move = None
    best_score = -math.inf if turn_white else math.inf

    # ─── endgame deepening ───
    piece_count = sum(1 for row in gs.board for sq in row if sq != '-')
    if depth == 4:
        if piece_count <= 4:
            depth = 8
        elif piece_count <= 7:
            depth = 6
    #print(f"findBestMove: depth={depth}, piece_count={piece_count}, turn_white={turn_white}")

    # ─── mate-in-1 shortcut ───
    for m in moves:
        gs.makeMove(m)
        if not gs.getValidMoves() and gs.inCheck():
            gs.undoMove()
            return_queue.put(m)
            return
        gs.undoMove()

    # ─── define your recursive search ───
    def minimax(node, d, alpha, beta, max_player):
        alpha_orig = alpha
        key = f"{node.current_fen}|{d}|{int(max_player)}"

        # TT Lookup with flags
        if key in _tt:
            entry = _tt[key]
            val, flag = entry["value"], entry["flag"]
            if flag == "EXACT":
                return val
            if flag == "LOWER" and val > alpha:
                alpha = val
            elif flag == "UPPER" and val < beta:
                beta = val
            if alpha >= beta:
                return val

        # generate moves & terminal tests
        legal = node.getValidMoves()
        if not legal:
            result = -CHECKMATE if node.inCheck() else STALEMATE
            val = (result if max_player else -result)
            _tt[key] = {"value": val, "flag": "EXACT"}
            return val
        
        # ─── null-move pruning (R=2) ───
        # only when depth > 2, not in check, and plenty of material left
        if d > 2 and not node.inCheck():
            non_king_count = sum(
                1
                for rr in range(8)
                for cc in range(8)
                if (sq := node.board[rr][cc]) != "-" and sq.upper() != "K"
            )
            if non_king_count > 6:
                # flip side without making a real move
                node.white_to_move = not node.white_to_move
                nm_val = -minimax(node, d - 3, -beta, -beta + 1, not max_player)
                node.white_to_move = not node.white_to_move
                if nm_val >= beta:
                    return nm_val
                
        # leaf evaluation
        if d == 0:
            # Count non-king pieces
            non_king_count = sum(
                1
                for rr in range(8)
                for cc in range(8)
                if (sq := node.board[rr][cc]) != "-" and sq.upper() != "K"
            )
            # Compute game stage
            stage = (MAX_NON_KING_PIECES - non_king_count) / MAX_NON_KING_PIECES
            stage = max(0.0, min(1.0, stage))

            score = 0
            for r in range(8):
                for c in range(8):
                    p = node.board[r][c]
                    if p == "-":
                        continue
                    pt = p.upper()
                    base = piece_score[pt]
                    # King blends midgame/endgame tables
                    if pt == "K":
                        mid_val = _tables["K"][r][c] if p.isupper() else _tables["K"][7 - r][c]
                        end_val = (
                            king_endgame_table[r][c]
                            if p.isupper()
                            else king_endgame_table[7 - r][c]
                        )
                        bonus = int(round((1.0 - stage) * mid_val + stage * end_val))
                    else:
                        bonus = (
                            _tables[pt][r][c]
                            if p.isupper()
                            else _tables[pt][7 - r][c]
                        )
                    score += (base + bonus) if p.isupper() else -(base + bonus)

            # Material‐difference‐based king bonuses
            mat_diff = sum(
                (piece_score[sq.upper()] if sq.isupper() else -piece_score[sq.upper()])
                for rr in range(8)
                for cc in range(8)
                if (sq := node.board[rr][cc]) != "-" and sq.upper() != "K"
            )
            sign = 1 if mat_diff > 0 else (-1 if mat_diff < 0 else 0)
            if sign != 0:
                wk_r, wk_c = node.white_king_location
                bk_r, bk_c = node.black_king_location
                dist_w = math.hypot(wk_r - 3.5, wk_c - 3.5)
                dist_b = math.hypot(bk_r - 3.5, bk_c - 3.5)
                center_term = stage * CENTER_WEIGHT * (dist_b - dist_w) * sign
                manh = abs(wk_r - bk_r) + abs(wk_c - bk_c)
                prox_term = stage * PROX_WEIGHT * (MAX_KING_MANHATTAN - manh) * sign
                score += int(round(center_term + prox_term))

            _tt[key] = {"value": score, "flag": "EXACT"}
            return score

        # PVS search
        value = -math.inf if max_player else math.inf
        first = True
        for m in legal:
            node.makeMove(m)
            if first:
                val = minimax(node, d - 1, alpha, beta, not max_player)
                first = False
            else:
                if max_player:
                    val = minimax(node, d - 1, alpha, alpha + 1, not max_player)
                    if alpha < val < beta:
                        val = minimax(node, d - 1, alpha, beta, not max_player)
                else:
                    val = minimax(node, d - 1, beta - 1, beta, not max_player)
                    if alpha < val < beta:
                        val = minimax(node, d - 1, alpha, beta, not max_player)
            node.undoMove()

            # update
            if max_player:
                value = max(value, val)
                alpha = max(alpha, value)
                if alpha >= beta:
                    if not m.is_capture:
                        km = _killers[d]
                        if m != km[0]:
                            km[1], km[0] = km[0], m
                    _history[(d, m.start_row, m.start_col, m.end_row, m.end_col)] += 2**d
                    break
            else:
                value = min(value, val)
                beta = min(beta, value)
                if beta <= alpha:
                    if not m.is_capture:
                        km = _killers[d]
                        if m != km[0]:
                            km[1], km[0] = km[0], m
                    _history[(d, m.start_row, m.start_col, m.end_row, m.end_col)] += 2**d
                    break

        # TT store
        if value <= alpha_orig:
            flag = "UPPER"
        elif value >= beta:
            flag = "LOWER"
        else:
            flag = "EXACT"
        _tt[key] = {"value": value, "flag": flag}
        return value

    # ─── iterative-deepening PV + root ordering ───
    pv_move = None
    for d_id in range(1, depth):
        best_sh, best_sh_score = None, (-math.inf if gs.white_to_move else math.inf)
        for m in moves:
            gs.makeMove(m)
            sc = minimax(gs, d_id - 1, -math.inf, math.inf, not gs.white_to_move)
            gs.undoMove()
            if (gs.white_to_move and sc > best_sh_score) or (not gs.white_to_move and sc < best_sh_score):
                best_sh_score, best_sh = sc, m
        pv_move = best_sh
    if pv_move in moves:
        moves.remove(pv_move)
        moves.insert(0, pv_move)

    def root_move_key(m):
        sc = 0
        if m.is_capture:
            sc += 10000 + piece_score[m.piece_captured.upper()] - piece_score[m.piece_moved.upper()]
        else:
            km0, km1 = _killers[depth]
            if m == km0:
                sc += 9000
            elif m == km1:
                sc += 8000
            sc += _history[(depth, m.start_row, m.start_col, m.end_row, m.end_col)]
        return sc

    # sort the rest after PV
    if pv_move:
        tail = moves[1:]
        tail.sort(key=root_move_key, reverse=True)
        moves[1:] = tail
    else:
        moves.sort(key=root_move_key, reverse=True)

    # ─── root search ───
    for m in moves:
        gs.makeMove(m)
        sc = minimax(gs, depth - 1, -math.inf, math.inf, not turn_white)
        gs.undoMove()
        if (turn_white and sc > best_score) or (not turn_white and sc < best_score):
            best_score, best_move = sc, m

    if best_move is None and moves:
        best_move = random.choice(moves)
    return_queue.put(best_move)
