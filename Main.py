"""
Main driver file.
Handling user input.
Displaying current GameStatus object.
"""
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1" # pd: partito democatico
import pygame as p
import pygame.scrap
import TAL, EVA
from EVA import piece_score, _tables, findBestMove
import sys
from multiprocessing import Process, Queue
import tkinter as _tk  # fallback for clipboard on macOS/Linux
import pyperclip

BOARD_WIDTH = BOARD_HEIGHT = 512
MOVE_LOG_PANEL_WIDTH = 250
LEFT_PANEL_WIDTH = 320  
FILE_BAR_HEIGHT  = 30
TOTAL_HEIGHT = BOARD_HEIGHT + FILE_BAR_HEIGHT
MOVE_LOG_PANEL_HEIGHT = BOARD_HEIGHT 
TOP_OFFSET = 10
DIMENSION = 8
SQUARE_SIZE = BOARD_HEIGHT // DIMENSION
MAX_FPS = 15
IMAGES = {}
EVAL_GREEN = (0, 255, 0)
EVAL_RED = (255, 0, 0)
EVAL_WHITE = (255, 255, 255)
MARGIN_X = 20
MARGIN_Y = 10
VERTICAL_OFFSET = 15 
# FONT_LARGE = p.font.SysFont("Consolas", 24, bold=True)
# FONT_MEDIUM = p.font.SysFont("Arial", 18)
# FONT_SMALL = p.font.SysFont("Arial", 14)


def loadImages():

    
    """
    Initialize a global directory of images.
    This will be called exactly once in the main.
    """
    pieces = ['P', 'R', 'N', 'B', 'K', 'Q', 'p', 'r', 'n', 'b', 'k', 'q']
    for piece in pieces:
        folder = "white" if piece.isupper() else "black"
        IMAGES[piece] = p.transform.scale(
            p.image.load("images/" + folder + "/" + piece.upper() + ".png"),
            (SQUARE_SIZE, SQUARE_SIZE)
        )
    # ── Easter‐egg knight promotion sprites ──
    IMAGES['L'] = p.transform.scale(
        p.image.load("images/white/EasterEggWhite.png"),
        (SQUARE_SIZE, SQUARE_SIZE)
    )
    IMAGES['l'] = p.transform.scale(
        p.image.load("images/black/EasterEggBlack.png"),
        (SQUARE_SIZE, SQUARE_SIZE)
    )


def get_clipboard_text():
    """
    Cross-platform clipboard text retrieval using pyperclip.
    """
    try:
        return pyperclip.paste()
    except Exception:
        return 
    
    """Return clipboard text; on macOS/Linux use tkinter, else try pygame.scrap."""
    # macOS or Linux: use tkinter
    # if sys.platform.startswith(("darwin", "linux")):
    #     try:
    #         root = _tk.Tk()
    #         root.withdraw()
    #         text = root.clipboard_get()
    #         root.destroy()
    #         return text 
    #     except Exception:
    #         pass
    # # fallback to pygame.scrap on Windows
    # try:
    #     clip = p.scrap.get(p.SCRAP_TEXT)
    #     if clip:
    #         return clip.decode('utf-8', 'ignore')
    # except Exception:
    #     pass
    # return ""


def main():
    p.init()
    p.font.init()
    global FONT_LARGE, FONT_MEDIUM, FONT_SMALL
    # bump up base font sizes
    FONT_LARGE  = p.font.SysFont("Consolas", 20, bold=True)  
    FONT_MEDIUM = p.font.SysFont("Arial",   30)              
    FONT_SMALL  = p.font.SysFont("Arial",   18)              

    icon = p.image.load("images/black/k.png")
    p.display.set_icon(icon)
    p.display.set_caption("TAL")

    # initialize true fullscreen and recalc board/UI sizes
    screen = p.display.set_mode((0, 0), p.FULLSCREEN)
    screen_width, screen_height = screen.get_size()
    available_h = screen_height - FILE_BAR_HEIGHT - MARGIN_Y
    available_w = screen_width  - LEFT_PANEL_WIDTH - MOVE_LOG_PANEL_WIDTH - MARGIN_X
    board_size   = min(available_h, available_w)
    global BOARD_WIDTH, BOARD_HEIGHT, SQUARE_SIZE, RIGHT_PANEL_WIDTH, X_OFFSET
    BOARD_WIDTH  = BOARD_HEIGHT = board_size
    SQUARE_SIZE  = board_size // DIMENSION

    # shrink right panel to 1/4 of board, center total UI
    RIGHT_PANEL_WIDTH = board_size // 4
    total_w = LEFT_PANEL_WIDTH  + BOARD_WIDTH + RIGHT_PANEL_WIDTH
    X_OFFSET = (screen_width - total_w) // 2 

    p.scrap.init()
    clock = p.time.Clock()
    prev_time = p.time.get_ticks()            # ← initialize real‐time reference

    game_state = TAL.GameState()
    valid_moves = game_state.getValidMoves()
    loadImages()

    move_made = False
    animate   = False

    white_time, black_time = 600, 600
    timeout_message = ""
    running = True
    square_selected = ()
    player_clicks = []
    game_over = ai_thinking = move_undone = False
    move_finder_process = None
    move_log_font = FONT_MEDIUM
    player_one, player_two = True, False
    fen_input_mode, fen_input_text = False, ""
    font_input = p.font.SysFont("Consolas", 20)

    while running:
        # compute actual elapsed seconds
        curr_time = p.time.get_ticks()
        dt = (curr_time - prev_time) / 1000.0
        prev_time = curr_time

        # update clocks
        if game_state.white_to_move:
            white_time -= dt
            if white_time <= 0:
                timeout_message, game_over = "White loses on time", True
        else:
            black_time -= dt
            if black_time <= 0:
                timeout_message, game_over = "Black loses on time", True

        human_turn = (game_state.white_to_move and player_one) or (not game_state.white_to_move and player_two)

        for e in p.event.get():
            if fen_input_mode:
                if e.type == p.KEYDOWN:
                    mods = e.mod
                    if e.key == p.K_RETURN:
                        # sanitize FEN: replace NBSP and collapse whitespace
                        raw = fen_input_text or TAL.INITIAL_FEN
                        clean = raw.replace('\xa0', ' ')
                        fen = ' '.join(clean.split())
                        game_state = TAL.GameState(fen)
                        valid_moves = game_state.getValidMoves()
                        fen_input_mode = False
                    #     fen_input_text = ""
                    # elif e.key == p.K_BACKSPACE:
                    #     fen_input_text = fen_input_text[:-1]
                    # elif e.key == p.K_v and (mods & p.KMOD_CTRL):
                    #     text = get_clipboard_text()
                        fen_input_text = ""
                    elif e.key == p.K_BACKSPACE:
                        fen_input_text = fen_input_text[:-1]
                    elif (e.key == p.K_v and (mods & p.KMOD_CTRL)) or (e.key == p.K_v and (mods & p.KMOD_META)):
                        text = get_clipboard_text()

                        # remove null chars before appending
                        fen_input_text += text.replace('\x00', '')
                    elif len(e.unicode) and e.unicode.isprintable():
                        fen_input_text += e.unicode
                continue  # skip normal input while typing FEN

            if e.type == p.QUIT:
                p.quit()
                sys.exit()
            # mouse handler
            elif e.type == p.MOUSEBUTTONDOWN:
                if not game_over:
                    mx, my = p.mouse.get_pos()
                    # adjust for centered board
                    board_x = mx - X_OFFSET - LEFT_PANEL_WIDTH
                    board_y = my - VERTICAL_OFFSET # board starts at top
                    
                    # only proceed if click is inside the 8×8 board
                    if 0 <= board_x < BOARD_WIDTH and 0 <= board_y < BOARD_HEIGHT:
                        col = board_x // SQUARE_SIZE
                        row = board_y // SQUARE_SIZE

                        # now (row,col) is exactly 0..7
                        if square_selected == (row, col):
                            square_selected = ()
                            player_clicks   = []
                        else:
                            square_selected = (row, col)
                            player_clicks.append(square_selected)

                        if len(player_clicks) == 2 and human_turn:
                            move = TAL.Move(player_clicks[0], player_clicks[1], game_state.board)
                            
                            for m in valid_moves:
                                if move == m:
                                    # If this is a pawn hitting the last rank, ask what to promote to
                                    if m.is_pawn_promotion:
                                        # pass in whichever font you like (e.g. FONT_MEDIUM)
                                        promo = getPromotionChoice(
                                            screen,
                                            FONT_MEDIUM,
                                            'white' if m.piece_moved.isupper() else 'black'
                                        )
                                        m.promotion_choice = promo
                                    game_state.makeMove(m)
                                    move_made = True
                                    animate   = True
                                    square_selected = ()
                                    player_clicks   = []
                                    break
                            else:
                                # invalid second click: reset to the new square
                                player_clicks = [square_selected]
                    if not (0 <= col < 8 and 0 <= row < 8):
                        continue

            # key handler
            elif e.type == p.KEYDOWN:
                if e.key == p.K_z:  # undo when 'z' is pressed
                    # undo both sides (AI + human) if you're doing full undos; otherwise just one
                    game_state.undoMove()
                    game_state.undoMove()
                    # recalc moves and clear all turn/animation flags
                    valid_moves = game_state.getValidMoves()
                    move_made    = False
                    animate      = False
                    move_undone  = False   # allow AI to respond to your next move
                    ai_thinking  = False

                if e.key == p.K_r:  # reset the game when 'r' is pressed
                    game_state = TAL.GameState()
                    valid_moves = game_state.getValidMoves()
                    square_selected = ()
                    player_clicks = []
                    move_made = False
                    animate = False
                    game_over = False
                    move_undone = False            # ← ADD THIS
                    ai_thinking = False            # make sure any old AI‐threads are off
                    white_time = 600               # reset timers
                    black_time = 600
                    timeout_message = ""           # clear any “time’s up” message
                    # if there was a background process still alive, kill it
                    if 'move_finder_process' in locals() and move_finder_process.is_alive():
                        move_finder_process.terminate()
                if e.key == p.K_f:  # start FEN entry
                    fen_input_mode = True
                    fen_input_text = ""
                    continue
                if e.key == p.K_q:  # quit on 'q'
                    p.quit()
                    sys.exit()


        if not game_over and not human_turn and not move_undone:
            if not ai_thinking:
                ai_thinking = True
                return_queue = Queue()  # used to pass data between threads
                move_finder_process = Process(target=EVA.findBestMove, args=(game_state, valid_moves, return_queue))
                move_finder_process.start()

            if not move_finder_process.is_alive():
                ai_move = return_queue.get()
                if ai_move is None:
                    ai_move = EVA.findRandomMove(valid_moves)
                game_state.makeMove(ai_move)
                move_made = True
                animate = True
                ai_thinking = False

        if move_made:
            if animate:
                animateMove(game_state.move_log[-1], screen, game_state, clock)
            valid_moves = game_state.getValidMoves()
            castles = [m for m in valid_moves if m.is_castle_move]
            move_made = False
            animate    = False
            # print("Castling rights right now:",
            #     game_state.current_castling_rights.wks,
            #     game_state.current_castling_rights.wqs,
            #     game_state.current_castling_rights.bks,
            #     game_state.current_castling_rights.bqs)
            # print("Valid castle moves currently:", [(m.start_row,m.start_col,m.end_row,m.end_col) for m in castles])



        screen.fill(p.Color("black"))
        drawGameState(screen, game_state, valid_moves, square_selected)
        drawLeftSidebar(screen, game_state)
        drawRightSidebar(screen, game_state, white_time, black_time)
        # drawEvalAndSuggestion(screen, game_state, move_log_font)
        # drawMoveLog(screen, game_state, move_log_font)
        # drawTimers(screen, white_time, black_time, move_log_font)
        if fen_input_mode:
            drawFenInput(screen, fen_input_text, font_input)
        if game_state.checkmate:
            game_over = True
            if game_state.white_to_move:
                drawEndGameText(screen, "Black wins by checkmate")
            else:
                drawEndGameText(screen, "White wins by checkmate")
        elif game_state.stalemate:
            game_over = True
            drawEndGameText(screen, "Draw")
        elif timeout_message:
            drawEndGameText(screen, timeout_message)

        p.display.flip()

def drawGameState(screen, game_state, valid_moves, square_selected):
    """
    Draw just the 8×8 board, the highlights, and the pieces.
    The left & right sidebars are drawn separately.
    """
    # 1) the squares + rank-files
    drawBoard(screen)

    # 2) highlighted last move & legal moves
    highlightSquares(screen, game_state, valid_moves, square_selected)

    # 3) pieces
    drawPieces(screen, game_state.board, game_state)


def drawBoard(screen):
    # 1) squares
    colors = [p.Color("ivory"), p.Color("lightskyblue")]
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            sq = p.Rect(
                X_OFFSET + LEFT_PANEL_WIDTH + c * SQUARE_SIZE,
                r * SQUARE_SIZE + VERTICAL_OFFSET,
                SQUARE_SIZE, SQUARE_SIZE
            )
            p.draw.rect(screen, colors[(r + c) % 2], sq)

    # 2) file‐letter bar under the board
    bar_y = BOARD_HEIGHT + VERTICAL_OFFSET
    p.draw.rect(screen, p.Color("black"),
                (X_OFFSET + LEFT_PANEL_WIDTH, bar_y,
                 BOARD_WIDTH, FILE_BAR_HEIGHT))
    for idx, f in enumerate("abcdefgh"):
        lbl = FONT_MEDIUM.render(f, True, EVAL_WHITE)
        x = X_OFFSET + LEFT_PANEL_WIDTH + idx * SQUARE_SIZE + (SQUARE_SIZE - lbl.get_width()) // 2
        y = bar_y + (FILE_BAR_HEIGHT - lbl.get_height()) // 2 
        screen.blit(lbl, (x, y))


def highlightSquares(screen, game_state, valid_moves, sq_sel):
    x0 = X_OFFSET + LEFT_PANEL_WIDTH
    # last move in translucent green
    if game_state.move_log:
        last = game_state.move_log[-1]
        s = p.Surface((SQUARE_SIZE, SQUARE_SIZE), p.SRCALPHA)
        s.fill((0,255,0,100))
        screen.blit(s, (x0 + last.end_col * SQUARE_SIZE,
                        last.end_row * SQUARE_SIZE + VERTICAL_OFFSET))
    # selected piece + its moves
    if sq_sel:
        r,c = sq_sel
        piece = game_state.board[r][c]
        if piece != '-' and ((piece.isupper() and game_state.white_to_move)
                             or (piece.islower() and not game_state.white_to_move)):
            # highlight the selected square in blue
            s = p.Surface((SQUARE_SIZE, SQUARE_SIZE), p.SRCALPHA)
            s.fill((0,0,255,100))
            screen.blit(s, (x0 + c * SQUARE_SIZE, r * SQUARE_SIZE + VERTICAL_OFFSET))
            # highlight each legal destination in yellow
            s.fill((255,255,0,100))
            for m in valid_moves:
                if m.start_row == r and m.start_col == c:
                    screen.blit(s, (x0 + m.end_col * SQUARE_SIZE,
                                    m.end_row * SQUARE_SIZE + VERTICAL_OFFSET))

def drawPieces(screen, board, game_state):
    x0 = X_OFFSET + LEFT_PANEL_WIDTH
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            pch = board[r][c]
            if pch != '-':
                # if this square was a pawn→knight promo, use the easter‐egg sprite
                # if pch.upper() == 'N' and (r, c) in game_state.easter_knights:
                #     key = 'N_promo' if pch.isupper() else 'n_promo'
                # else:
                #     key = pch
                key = pch
                screen.blit(
                    IMAGES[key],
                    p.Rect(x0 + c * SQUARE_SIZE,
                           r * SQUARE_SIZE + VERTICAL_OFFSET,
                           SQUARE_SIZE, SQUARE_SIZE)
                )


def drawEndGameText(screen, text):
    font = p.font.SysFont("Helvetica", 32, True, False)
    to = font.render(text, True, p.Color("black"))
    w,h = to.get_size()
    x0 = LEFT_PANEL_WIDTH + (BOARD_WIDTH - w)//2
    y0 = (BOARD_HEIGHT - h)//2
    ts = font.render(text, True, p.Color("gray"))
    screen.blit(ts, (x0+2, y0+2))
    screen.blit(to, (x0, y0))


def evaluateBoard(game_state):
    score = 0
    for r in range(8):
        for c in range(8):
            p = game_state.board[r][c]
            if p == "-":
                continue
            pt = p.upper()
            base = piece_score[pt]
            bonus = _tables[pt][r][c] if p.isupper() else _tables[pt][7 - r][c]
            score += base + bonus if p.isupper() else -(base + bonus)
    return score


def drawLeftSidebar(screen, game_state):
    """
    Left panel:  two‐column SAN history, larger fonts.
    """
    # 1) background
    p.draw.rect(screen, p.Color('black'),
                (X_OFFSET, 0, LEFT_PANEL_WIDTH, BOARD_HEIGHT))
    # 2) rank labels
    indent = 20
    for idx, rank in enumerate("87654321"):
        lbl = FONT_MEDIUM.render(rank, True, EVAL_WHITE)
        x_lbl = X_OFFSET + LEFT_PANEL_WIDTH - indent - lbl.get_width()
        y_lbl = idx * SQUARE_SIZE + (SQUARE_SIZE - lbl.get_height()) // 2
        screen.blit(lbl, (x_lbl, y_lbl))

    # 3) two‐column SAN move history
    san_list = []
    temp = TAL.GameState()
    for m in game_state.move_log:
        temp.makeMove(m); temp.getValidMoves()
        txt = m.getChessNotation().replace("e.p.","")
        if temp.checkmate: txt += "#"
        elif temp.inCheck(): txt += "+"
        san_list.append(txt)

    # build paired moves: "1. e4 e5", etc.
    lines = []
    for i in range(0, len(san_list), 2):
        line = f"{i//2+1}. {san_list[i]}"
        if i+1 < len(san_list):
            line += f" {san_list[i+1]}"
        lines.append(line)

    # print two paired moves per row, chronologically
    indent = 20
    half_width = LEFT_PANEL_WIDTH // 2
    fh = FONT_LARGE.get_height()
    sp = 6
    for i in range(0, len(lines), 2):
        row = i // 2
        y = TOP_OFFSET + row * (fh + sp)
        # left move in pair
        surf = FONT_LARGE.render(lines[i], True, EVAL_WHITE)
        screen.blit(surf, (X_OFFSET + indent, y))
        # right move, if exists
        if i + 1 < len(lines):
            surf2 = FONT_LARGE.render(lines[i+1], True, EVAL_WHITE)
            screen.blit(surf2, (X_OFFSET + half_width , y))


def drawRightSidebar(screen, game_state, white_time, black_time):
    """
    Right panel (LEFT_PANEL_WIDTH+BOARD_WIDTH → end):
      timers, eval, best, turn, cmds.
    """
    px = X_OFFSET + LEFT_PANEL_WIDTH + BOARD_WIDTH
    p.draw.rect(screen, p.Color('black'),
                (px, 0, RIGHT_PANEL_WIDTH, BOARD_HEIGHT))

    x = px + 10 
    y = 10
    fh = FONT_MEDIUM.get_height()
    sp = 10

    # timers
    w_m, w_s = divmod(int(white_time), 60)
    b_m, b_s = divmod(int(black_time), 60)
    lines = [f"White: {w_m:02}:{w_s:02}", f"Black: {b_m:02}:{b_s:02}"]

    # Commented out evaluation display
    # score = evaluateBoard(game_state)
    # lines.append(f"Eval: {'+' if score>=0 else ''}{score/100:.2f}")

    # Best move suggestion: only shown on human turn (assumed for white)
    if game_state.white_to_move:
        moves = game_state.getValidMoves()
        suggestion = "-"
        if moves:
            q = Queue()
            EVA.findBestMove(game_state, moves, q, depth=2)
            best = q.get()
            if best:
                suggestion = best.getChessNotation()
                # simulate on a fresh state
                temp = TAL.GameState(game_state.current_fen)
                temp.makeMove(best)
                temp.getValidMoves()
                if temp.checkmate:
                    suggestion += "#"
                elif temp.inCheck():
                    suggestion += "+"
    else:
        suggestion = ""
    lines.append(f"Best: {suggestion}")

    # turn
    lines.append(f"Turn: {'White' if game_state.white_to_move else 'Black'}")

    max_items = (BOARD_HEIGHT - y - 10) // (fh + sp)
    # draw info lines (timers, best, turn)
    for idx, line in enumerate(lines[-max_items:]):
        if idx < 2:
            color = p.Color('red')
        elif idx == 2:
            # Always set best move suggestion's color to green
            color = EVAL_GREEN
        elif idx <= 4:
            color = EVAL_WHITE
        else:
            color = p.Color('gray')
        surf = FONT_MEDIUM.render(line, True, color)
        screen.blit(surf, (x, y + idx*(fh+sp)))
    # end existing loop

    # draw commands at the bottom
    commands = ["f: load FEN", "r: restart", "z: undo", "q: quit"]
    bottom_margin = 10
    for i, cmd in enumerate(commands):
        y_cmd = BOARD_HEIGHT - bottom_margin - (len(commands)-i)*(fh + sp)
        surf = FONT_MEDIUM.render(cmd, True, p.Color('gray'))
        screen.blit(surf, (x, y_cmd))


def getPromotionChoice(screen, font, piece_color):
    """
    Draw an overlay on screen asking the user to choose a promotion piece.
    Returns the chosen piece letter ("Q", "R", "B", or "N").
    """
    options = ["Q", "R", "B", "N"]
    overlay = p.Surface((300, 100))
    overlay.fill(p.Color("gray"))
    # center inside the board, just like drawFenInput does
    board_x0 = X_OFFSET + LEFT_PANEL_WIDTH
    board_y0 = VERTICAL_OFFSET
    overlay_rect = overlay.get_rect(
        center=(board_x0 + BOARD_WIDTH//2, board_y0 + BOARD_HEIGHT//2)
    )
    screen.blit(overlay, overlay_rect)
    
    option_rects = []
    spacing = 10
    option_width = (300 - 5 * spacing) / 4
    y = overlay_rect.top + 20
    for i, option in enumerate(options):
        rect = p.Rect(
            overlay_rect.left + spacing + i*(option_width+spacing),
            y,
            option_width,
            60
        )
        p.draw.rect(screen, p.Color("white"), rect)
        text_surface = font.render(option, True, p.Color("black"))
        text_rect = text_surface.get_rect(center=rect.center)
        screen.blit(text_surface, text_rect)
        option_rects.append((option, rect))
    p.display.flip()
    
    while True:
        for event in p.event.get():
            if event.type == p.MOUSEBUTTONDOWN:
                pos = event.pos
                for option, rect in option_rects:
                    if rect.collidepoint(pos):
                        return option



def drawFenInput(screen, text, font):
    """Draw a centered, bordered input box and current FEN text."""
    box_w, box_h = BOARD_WIDTH - 100, 40
    # center within board (account for horizontal & vertical offsets)
    board_x0 = X_OFFSET + LEFT_PANEL_WIDTH
    board_y0 = VERTICAL_OFFSET
    x = board_x0 + (BOARD_WIDTH  - box_w)//2
    y = board_y0 + (BOARD_HEIGHT - box_h)//2
    # background
    overlay = p.Surface((box_w, box_h))
    overlay.set_alpha(220)
    overlay.fill(p.Color('black'))
    screen.blit(overlay, (x, y))
    # border
    p.draw.rect(screen, p.Color('white'), (x, y, box_w, box_h), 2)
    # text: sanitize and scroll if too wide
    prompt = text.replace('\x00', '') if text else "Paste or type FEN here..."
    max_w = box_w - 20
    display = prompt
    # trim front until it fits
    while font.size(display)[0] > max_w and len(display) > 1:
        display = display[1:]
    txt_surf = font.render(display, True, p.Color('white'))
    screen.blit(txt_surf, (x+10, y + (box_h - txt_surf.get_height())//2))

def animateMove(move, screen, game_state, clock):
    """
    Animate a move by sliding the moving piece from its start square to its end square.
    """
    frames_per_square = 10  # number of frames to move one square
    d_row = move.end_row - move.start_row
    d_col = move.end_col - move.start_col
    frame_count = (abs(d_row) + abs(d_col)) * frames_per_square

    # board colors must match drawBoard
    colors = [p.Color("ivory"), p.Color("lightskyblue")]
    board = game_state.board

    for frame in range(frame_count + 1):
        # fractional row/col for smooth interpolation
        row = move.start_row + d_row * frame / frame_count
        col = move.start_col + d_col * frame / frame_count

        # redraw board and all pieces
        drawBoard(screen)
        drawPieces(screen, board, game_state)

        # clear the destination square
        dst_rect = p.Rect(
            X_OFFSET + LEFT_PANEL_WIDTH + move.end_col * SQUARE_SIZE,
            VERTICAL_OFFSET + move.end_row     * SQUARE_SIZE,
            SQUARE_SIZE, SQUARE_SIZE
        )
        p.draw.rect(screen,
                    colors[(move.end_row + move.end_col) % 2],
                    dst_rect)

        # if it was a capture, redraw the captured piece (excluding empty "-")
        if move.piece_captured != '-':
            if move.is_enpassant_move:
                er = move.end_row + (1 if move.piece_captured[0] == 'b' else -1)
                cap_rect = p.Rect(
                    X_OFFSET + LEFT_PANEL_WIDTH + move.end_col * SQUARE_SIZE,
                    VERTICAL_OFFSET + er              * SQUARE_SIZE,
                    SQUARE_SIZE, SQUARE_SIZE
                )
            else:
                cap_rect = dst_rect
            screen.blit(IMAGES[move.piece_captured], cap_rect)

        # draw the moving piece at its interpolated position
        screen.blit(
            IMAGES[move.piece_moved],
            p.Rect(
                X_OFFSET + LEFT_PANEL_WIDTH + col * SQUARE_SIZE,
                VERTICAL_OFFSET + row             * SQUARE_SIZE,
                SQUARE_SIZE, SQUARE_SIZE
            )
        )

        p.display.flip()
        clock.tick(60)




if __name__ == "__main__":
    main()
