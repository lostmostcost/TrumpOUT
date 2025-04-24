from flask import render_template, request, session, redirect, url_for
from flask_socketio import emit, join_room
from models import Player, PLAYER_COUNT, MULLIGAN_COUNT, HAND_COUNT, FIRST_PLAYER_THRESHOLD, Game, game

def error_response(message):
    return f'''
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>오류</title>
    </head>
    <body>
        <p>{message}</p>
        <button onclick="history.back()">이전 화면으로 돌아가기</button>
    </body>
    </html>
    '''

# Error flash mechanism for game screen
def flash_error(msg):
    """Store a one‑shot error message to be sent with next socket payload."""
    session['error_message'] = msg

# key: player name -> {'pending': bool, 'cards': [html_str, ...]}
mulligan_info = {}

# J 사용 후 대상/버리기 미완료인 플레이어 목록
j_pending = {}

info_message = None  # last one-shot broadcast message (e.g., J discard)

def register_routes(app, socketio):
    def get_state_for(player_name):
        """특정 플레이어에게만 전송할 게임 상태 생성"""
        # 현재 라운드가 없으면 기본 메시지
        if not game.current_round:
            return {
                "message": "라운드가 종료되었습니다.",
                "players": [p.name for p in game.players],
            }
        # 숫자 데이터들
        recent_bets = [
            {
                "player": name,
                "value": value,
                "cards": [str(app.jinja_env.globals['card_to_html'](c)) for c in cards]
            }
            for name, cards, value, _ in game.current_round.bet_history[::-1]
        ]
        graph_data = [
            {"value": value, "reversed": (effect == "reversed")}
            for name, cards, value, effect in game.current_round.bet_history[::-1]
        ]
        # 해당 플레이어 손패만 포함
        hand = []
        player_obj = next((p for p in game.players if p.name == player_name), None)
        if player_obj:
            hand = [str(app.jinja_env.globals['card_to_html'](c)) for c in player_obj.hand]

        # 턴 순서 생성
        turn_order = []
        n = len(game.current_round.players)
        start = game.current_round.first_player_index
        for i in range(n):
            turn_order.append(game.current_round.players[(start + i) % n].name)

        # 다른 플레이어 정보
        players = {
            p.name: {
                "남은 패": len(p.hand),
                "누적 승점": p.total_points,
                "행동": p.last_action,
                "score_cards": [str(app.jinja_env.globals['card_to_html'](c)) for c in p.score_cards]
            }
            for p in game.players
        }

        return {
            "first_player": game.current_round.players[start].name,
            "current_turn": game.current_round.players[game.current_round.current_turn_index].name,
            "turn_order": turn_order,
            "recent_bets": recent_bets,
            "graph_data": graph_data,
            "total_rounds":     FIRST_PLAYER_THRESHOLD * len(game.players),
            "completed_rounds":  len(game.round_history),
            "remaining_rounds": FIRST_PLAYER_THRESHOLD * len(game.players) - sum(p.starting_count for p in game.players),
            "players": players,
            "pending_j": j_pending.get(player_name, False),
            "hand": hand,
            "mulligan_cards": mulligan_info.get(player_name, {}).get('cards') if mulligan_info.get(player_name, {}).get('pending') else None,
            "j_candidates": (j_pending.get(player_name, False) and 'j_target_hand' not in session)
                            and [p.name for p in game.players if p.name != player_name] or None,
            "j_target_hand": session.get('j_target_hand') if 'j_target' in session else None,
            "j_target": session.get('j_target') if session.get('j_target') else None,
            # 오류 메시지는 요청을 발생시킨 플레이어(세션의 player_name)에게만 전달
            "error_message": session.get("error_message") if player_name == session.get("player_name") else None,
            "game_over": game.game_over,
            "info_message": info_message,
        }

    def broadcast_game_state():
    # 각 플레이어 방(room)으로 전송
        for p in game.players:
            socketio.emit('game_state_update', get_state_for(p.name), room=p.name)
        # 게임 종료시 별도 이벤트 broadcast
        if game.game_over:
            for p in game.players:
                socketio.emit('game_over', {}, room=p.name)
        # 오류 메세지는 1회 표시 후 제거
        session.pop("error_message", None)
        global info_message
        info_message = None

    @app.route('/join', methods=['GET', 'POST'])
    def join():
        if request.method == 'POST':
            name = request.form.get('name')
            if not name:
                return error_response("이름을 입력하세요.")
            if len(game.players) >= PLAYER_COUNT and not any(p.name == name for p in game.players):
                return error_response(f"이미 {PLAYER_COUNT}명의 플레이어가 있습니다.")
            session['player_name'] = name
            if not any(p.name == name for p in game.players):
                new_player = Player(name)
                if not game.add_player(new_player):
                    return error_response("플레이어 추가 실패")
            broadcast_game_state()
            return redirect(url_for('index'))
        current_players = [p.name for p in game.players]
        return render_template("join.html", players=current_players, player_count=PLAYER_COUNT)

    @app.route('/')
    def index():
        if 'player_name' not in session:
            return redirect(url_for('join'))
        player_name = session['player_name']
        if game.game_over:
            final_scores = {p.name: p.total_points for p in game.players}
            return render_template("main.html", 
                                game_over=True,
                                final_scores=final_scores,
                                player_name=player_name,
                                pending_j=session.get("pending_j", False))
        if len(game.players) < PLAYER_COUNT:
            state = {"message": "아직 플레이어가 모이지 않았습니다.",
                    "players": [p.name for p in game.players]}
            return render_template("waiting.html", state=state, player_count=PLAYER_COUNT)
        if game.current_round is None:
            game.start_round()
            broadcast_game_state()
        return render_template("main.html", player_name=player_name, game_over=False, pending_j=session.get("pending_j", False))

    @app.route('/action', methods=['POST'])
    def action():
        if 'player_name' not in session:
            return redirect(url_for('join'))
        player_name = session['player_name']
        player = next((p for p in game.players if p.name == player_name), None)
        if game.current_round is None:
            return redirect(url_for('index'))
        cur_round = game.current_round
        current_turn = cur_round.players[cur_round.current_turn_index].name
        if player.name != current_turn:
            flash_error("아직 당신의 차례가 아닙니다.")
            broadcast_game_state()
            return ('', 204)
        
        action_type = request.form.get("action_type")
        if action_type == "raise":
            indices = request.form.getlist("card_indices")
            try:
                indices = [int(x) for x in indices]
            except ValueError:
                flash_error("유효한 카드 인덱스가 아닙니다.")
                broadcast_game_state()
                return ('', 204)
            success, message = cur_round.player_raise(player, indices)
            if not success:
                flash_error(message)
                broadcast_game_state()
                return ('', 204)
            if cur_round.bet_history[-1][3] == "J":
                # J 효과 대기 상태 등록
                j_pending[player.name] = True
                broadcast_game_state()
                return ('', 204)
        elif action_type == "fold":
            # If eligible for mulligan
            if not player.has_raised and not player.mulligan_used:
                new_cards = game.deck.draw(MULLIGAN_COUNT)
                player.hand.extend(new_cards)
                mulligan_info[player.name] = {
                    'pending': True,
                    'cards': [str(app.jinja_env.globals['card_to_html'](c)) for c in new_cards]
                }
                broadcast_game_state()
                return ('', 204)   # no full-page reload; updates via SocketIO
            # Otherwise perform fold
            success, message = cur_round.player_fold(player)
            if not success:
                flash_error(message)
                broadcast_game_state()
                return ('', 204)
        elif action_type == 'mulligan_discard':
            indices = request.form.getlist('discard_indices')
            indices = sorted([int(x) for x in indices], reverse=True)
            if len(indices) != MULLIGAN_COUNT:
                flash_error(f"{MULLIGAN_COUNT}장을 정확히 선택해야 합니다.")
                broadcast_game_state()
                return ('', 204)
            for idx in indices:
                try:
                    player.hand.pop(idx)
                except IndexError:
                    flash_error("잘못된 카드 인덱스입니다.")
                    broadcast_game_state()
                    return ('', 204)
            player.mulligan_used = True
            player.last_action = "멀리건"
            mulligan_info.pop(player.name, None)   # clear
            cur_round = game.current_round
            cur_round.player_fold(player)          # 라운드 이탈 취급
            if cur_round.check_round_over():
                game.end_round()
            else:
                cur_round.next_player()
            broadcast_game_state()
            return ('', 204)
        elif action_type == 'j_select':
            target = request.form.get('target')
            if not target:
                flash_error("유효한 대상을 선택해주세요.")
                broadcast_game_state()
                return ('', 204)
            # store hand HTML for selected target
            target_player = next(p for p in game.players if p.name == target)
            session['j_target'] = target
            session['j_target_hand'] = [str(app.jinja_env.globals['card_to_html'](c)) for c in target_player.hand]
            broadcast_game_state()
            return ('', 204)

        elif action_type == 'j_discard':
            indices = request.form.getlist('discard_indices')
            indices = [int(x) for x in indices]
            target_name = session.pop('j_target')
            target_player = next(p for p in game.players if p.name == target_name)
            for idx in sorted(indices, reverse=True):
                card_html = str(app.jinja_env.globals['card_to_html'](target_player.hand[idx]))
                target_player.hand.pop(idx)
            session.pop('j_target_hand', None)
            j_pending.pop(player_name, None)
            # broadcast info message to everyone
            global info_message
            info_message = f"{player_name} ▶ {target_name} 의 {card_html} 를 버렸습니다."
            # advance turn
            cur_round = game.current_round
            if cur_round.check_round_over():
                game.end_round()
            else:
                cur_round.next_player()
            broadcast_game_state()
            return ('', 204)
        else:
            flash_error("알 수 없는 행동입니다.")
            broadcast_game_state()
            return ('', 204)
        if cur_round.check_round_over():
            game.end_round()
        else:
            cur_round.next_player()
        broadcast_game_state()
        return redirect(url_for('index'))

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('join'))

    @app.route('/reset')
    def reset():
        global game, mulligan_info, j_pending
        game = Game()
        mulligan_info = {}
        j_pending = {}
        session.clear()
        broadcast_game_state()
        return redirect(url_for('join'))

    @socketio.on('connect')
    def handle_connect():
        player = session.get('player_name')
        if player:
            join_room(player)
            # 연결 직후 한 번 전송
            emit('game_state_update', get_state_for(player), room=player)