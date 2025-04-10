# routes.py
from flask import render_template, request, session, redirect, url_for, jsonify
from flask_socketio import emit
from models import Player, PLAYER_COUNT, MULLIGAN_COUNT, HAND_COUNT, game

def error_response(message):
    # 에러의 경우 HTML 템플릿 대신 간단한 문자열 HTML을 반환합니다.
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

# 전역 변수 (J 카드 효과 대상 공개 저장)
revealed_hands = {}

def register_routes(app, socketio):
    def get_current_game_state():
        if game.current_round:
            bet_history_serialized = []
            for entry in game.current_round.bet_history[::-1]:
                player_name, cards, num_sum, special_effect = entry
                bet_history_serialized.append((player_name, [str(app.jinja_env.globals['card_to_html'](c)) for c in cards], num_sum))
            
            # 턴 진행 순서 계산
            n = len(game.current_round.players)
            turn_order = []
            start = game.current_round.first_player_index
            for i in range(n):
                turn_order.append(game.current_round.players[(start + i) % n].name)
            
            state = {
                "first_player": game.current_round.players[game.current_round.first_player_index].name,
                "current_turn": game.current_round.players[game.current_round.current_turn_index].name,
                "turn_order": turn_order,
                "current_highest": game.current_round.current_highest,
                "bet_history": bet_history_serialized,
                "players": {p.name: {
                    "남은 패": len(p.hand),
                    "누적 승점": p.total_points,
                    "선플레이어 횟수": p.starting_count,
                    "행동": p.last_action,
                    "score_cards": [str(app.jinja_env.globals['card_to_html'](c)) for c in p.score_cards]
                } for p in game.players},
                "pending_j": session.get("pending_j", False),
                "round_over": False
            }
            
            # 현재 플레이어의 손패도 HTML 리스트로 준비 (옵션)
            current_player_name = session.get("player_name")
            my_hand = []
            if current_player_name:
                player_obj = next((p for p in game.players if p.name == current_player_name), None)
                if player_obj:
                    for i, card in enumerate(player_obj.hand):
                        item = f"<li><label><input type='checkbox' name='card_indices' value='{i}'> {app.jinja_env.globals['card_to_html'](card)}</label></li>"
                        my_hand.append(item)
            state["my_hand"] = my_hand
            
        else:
            state = {
                "message": "라운드가 종료되었습니다.",
                "players": [p.name for p in game.players],
                "round_over": True if game.round_history else False
            }
        return state

    def broadcast_game_state():
        state = get_current_game_state()
        socketio.emit('game_state_update', state)

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
        cur_round = game.current_round
        current_turn = cur_round.players[cur_round.current_turn_index].name
        turn_order = []
        n = len(cur_round.players)
        start = cur_round.first_player_index
        for i in range(n):
            turn_order.append(cur_round.players[(start + i) % n].name)
        my_turn = (player_name == current_turn)
        player = next((p for p in game.players if p.name == player_name), None)
        player_status = []
        for p in game.players:
            player_status.append({
                "이름": p.name,
                "남은 패": len(p.hand),
                "누적 승점": p.total_points,
                "선플레이어 횟수": p.starting_count,
                "행동": p.last_action,
                "score_cards": [app.jinja_env.globals['card_to_html'](c) for c in p.score_cards]
            })
        
        return render_template("main.html",
                            game_over=False,
                            player_name=player_name,
                            first_player=cur_round.players[cur_round.first_player_index].name,
                            hand_count=len(player.hand),
                            hand=player.hand,
                            score_cards=player.score_cards,
                            total_points=player.total_points,
                            bet_history=[(entry[0], [app.jinja_env.globals['card_to_html'](c) for c in entry[1]], entry[2])
                                            for entry in cur_round.bet_history[::-1]],
                            my_turn=my_turn,
                            round_history=game.round_history,
                            turn_order=turn_order,
                            current_turn=current_turn,
                            final_scores={},
                            player_status=player_status,
                            pending_j=session.get("pending_j", False))

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
            return error_response("아직 당신의 차례가 아닙니다.")
        action_type = request.form.get("action_type")
        if action_type == "raise":
            indices = request.form.getlist("card_indices")
            try:
                indices = [int(x) for x in indices]
            except ValueError:
                return error_response("유효한 카드 인덱스가 아닙니다.")
            target = request.form.get("target")
            if target:
                target = target.strip()
            else:
                target = None
            success, message = cur_round.player_raise(player, indices, target_player=target)
            if not success:
                return error_response(message)
        elif action_type == "fold":
            if not player.has_raised and not player.mulligan_used:
                return redirect(url_for('mulligan'))
            else:
                success, message = cur_round.player_fold(player)
                if not success:
                    return error_response(message)
        else:
            return error_response("알 수 없는 행동입니다.")
        if cur_round.check_round_over():
            game.end_round()
        else:
            cur_round.next_player()
        broadcast_game_state()
        return redirect(url_for('index'))

    @app.route('/mulligan', methods=['GET', 'POST'])
    def mulligan():
        if 'player_name' not in session:
            return redirect(url_for('join'))
        player_name = session['player_name']
        player = next((p for p in game.players if p.name == player_name), None)
        cur_round = game.current_round
        if request.method == 'POST':
            selected = request.form.getlist("discard_indices")
            try:
                discard_indices = [int(x) for x in selected]
            except ValueError:
                return error_response("유효한 카드 인덱스가 아닙니다.")
            if len(discard_indices) != MULLIGAN_COUNT:
                return error_response(f"정확히 {MULLIGAN_COUNT}장의 카드를 선택해야 합니다.")
            for index in sorted(discard_indices, reverse=True):
                try:
                    player.hand.pop(index)
                except IndexError:
                    return error_response("잘못된 카드 인덱스입니다.")
            if cur_round and player.name in cur_round.active_players:
                del cur_round.active_players[player.name]
            player.mulligan_used = True
            player.last_action = "멀리건(자동 폴드)"
            if cur_round.check_round_over():
                game.end_round()
            else:
                cur_round.next_player()
            broadcast_game_state()
            return redirect(url_for('index'))
        else:
            new_cards = game.deck.draw(MULLIGAN_COUNT)
            player.hand.extend(new_cards)
            total_cards = HAND_COUNT + MULLIGAN_COUNT
            return render_template("mulligan.html", hand=player.hand, mulligan_count=MULLIGAN_COUNT, total_cards=total_cards)

    @app.route('/j_select', methods=['GET', 'POST'])
    def j_select():
        if 'player_name' not in session:
            return redirect(url_for('join'))
        current_player = session['player_name']
        candidate_names = [p.name for p in game.players if p.name != current_player]
        if request.method == 'POST':
            target = request.form.get('target')
            if not target or target not in candidate_names:
                return error_response("유효한 대상을 선택해주세요.")
            target_player = next((p for p in game.players if p.name == target), None)
            if target_player:
                revealed_hands[current_player] = (target, [str(app.jinja_env.globals['card_to_html'](c)) for c in target_player.hand])
            session.pop("pending_j", None)
            return redirect(url_for('j_view'))
        return render_template("j_select.html", candidates=candidate_names)

    @app.route('/j_view')
    def j_view():
        if 'player_name' not in session:
            return redirect(url_for('join'))
        current_player = session['player_name']
        target_info = revealed_hands.get(current_player)
        if not target_info:
            return error_response("J 카드 효과로 공개된 대상이 없습니다.")
        target_name, hand_html_list = target_info
        return render_template("j_view.html", target_name=target_name, hand=hand_html_list)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('join'))

    @app.route('/reset')
    def reset():
        global game, revealed_hands
        from models import Game  # 재생성을 위해 모델에서 Game 클래스를 불러옴
        game = Game()
        revealed_hands = {}
        session.clear()
        broadcast_game_state()
        return redirect(url_for('join'))

    @app.route('/game_state')
    def game_state():
        return jsonify(get_current_game_state())

    @socketio.on('connect')
    def handle_connect():
        emit('game_state_update', get_current_game_state())