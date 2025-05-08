from flask import render_template, request, session, redirect, url_for
from flask_socketio import emit, join_room
from models import Player, Game, GameConfig
import secrets

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

# ── Multi‑room containers ───────────────────────────
waiting_rooms = {}   # room_id -> {'config': GameConfig, 'joiners': [str]}
games         = {}   # room_id -> Game

def current_game():
    rid = session.get('room_id')
    return games.get(rid)

# Helper: unique socket room for a player in a game
def player_room(game: Game, player) -> str:
    """Return unique socket room for a player inside its game."""
    return f"{game.room_id}:{player.id}"

def register_routes(app, socketio):
    def get_state_for(game, player_name):
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
        pid = player_obj.id if player_obj else None
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
            "total_rounds":     game.cfg.first_player_threshold * len(game.players),
            "completed_rounds":  len(game.round_history),
            "remaining_rounds": game.cfg.first_player_threshold * len(game.players) - sum(p.starting_count for p in game.players),
            "players": players,
            "pending_j": game.j_pending.get(pid, False),
            "hand": hand,
            "mulligan_cards": (
                game.mulligan_info.get(pid, {}).get("cards")
                if game.mulligan_info.get(pid, {}).get("pending") else None
            ),
            "j_candidates": (game.j_pending.get(pid, False) and 'j_target_hand' not in session)
                            and [p.name for p in game.players if p.name != player_name] or None,
            "j_target_hand": session.get('j_target_hand') if 'j_target' in session else None,
            "j_target": session.get('j_target') if session.get('j_target') else None,
            # 오류 메시지는 요청을 발생시킨 플레이어(세션의 player_name)에게만 전달
            "error_message": session.get("error_message") if player_name == session.get("player_name") else None,
            "game_over": game.game_over,
            "info_message": game.info_message,
        }

    def broadcast_game_state(game):
        # 각 플레이어 방(room)으로 전송
        for p in game.players:
            socketio.emit(
                'game_state_update',
                get_state_for(game, p.name),
                room=player_room(game, p)
            )
        # 게임 종료시 별도 이벤트 broadcast
        if game.game_over:
            for p in game.players:
                socketio.emit('game_over', {}, room=player_room(game, p))
        # 오류 메세지는 1회 표시 후 제거
        session.pop("error_message", None)
        game.info_message = None

    # --- 실시간 로비 인원 목록 브로드캐스트 ------------------------
    def broadcast_lobby_state(room_id):
        if room_id in waiting_rooms:
            info = waiting_rooms[room_id]
            socketio.emit(
                "lobby_update",
                {
                    "room": room_id,
                    "players": info["joiners"],
                    "self_joined": session.get("player_name") in info["joiners"],
                },
                room=room_id,
            )

    # --- 게임 중단 → 클라이언트를 로비로 되돌리기 ------------------
    def broadcast_lobby_restart(room_id: str):
        """Emit lobby_restart so all game clients redirect to lobby page."""
        socketio.emit("lobby_restart", {"room": room_id}, room=room_id)

    @app.route('/create', methods=['POST'])
    def create():
        # This route only handles POST submissions from the inline create panel.
        cfg = GameConfig(
            title       = request.form.get('title') or "Trump OUT Room",
            private     = bool(request.form.get('private')),
            player_count= int(request.form.get('player_count', 4)),
            hand_count  = int(request.form.get('hand_count', 6)),
            mulligan_count = int(request.form.get('mulligan_count', 4)),
            max_cards_per_play = int(request.form.get('max_cards_per_play', 2)),
            first_player_threshold = int(request.form.get('first_player_threshold', 5))
        )
        room_id = secrets.token_hex(3)
        waiting_rooms[room_id] = {
            'config': cfg,
            'joiners': [request.form.get('host_name')]
        }
        session['player_name'] = request.form.get('host_name')
        session['room_id']     = room_id
        broadcast_lobby_state(room_id)
        return redirect(url_for('lobby', room_id=room_id))

    @app.route('/lobby/<room_id>')
    def lobby(room_id):
        wr = waiting_rooms.get(room_id)
        if not wr:
            return error_response("방을 찾을 수 없습니다.")
        return render_template(
            "lobby.html",
            cfg        = wr['config'],
            players    = wr['joiners'],
            room_id    = room_id,
            self_name  = session.get("player_name"),
            left_message = wr.get('last_left')
        )

    @app.route('/rooms')
    def rooms():
        public_rooms = {rid:info for rid,info in waiting_rooms.items()
                        if not info['config'].private}
        return render_template('rooms.html', rooms=public_rooms)

    @app.route('/join/<room_id>', methods=['GET', 'POST'])
    def join(room_id):
        wr_game = games.get(room_id)
        wr_wait = waiting_rooms.get(room_id)
        # 이미 이 방에 참가 세션이 존재하면 바로 index로
        if session.get('room_id') == room_id and session.get('player_name'):
            return redirect(url_for('index'))
        if request.method == 'POST':
            name = request.form.get('name')
            if not name:
                return error_response("이름을 입력하세요.")
            if wr_game:
                # Ongoing game: only allow if name exists in players
                if not any(p.name == name for p in wr_game.players):
                    return error_response("진행중인 게임에 참가할 수 없습니다.")
            elif wr_wait:
                if name not in wr_wait['joiners']:
                    wr_wait['joiners'].append(name)
                if len(wr_wait['joiners']) == wr_wait['config'].player_count:
                    g = Game(wr_wait['config'], room_id)
                    for n in wr_wait['joiners']:
                        g.add_player(Player(n))
                    games[room_id] = g
                    waiting_rooms.pop(room_id, None)
            else:
                return error_response("잘못된 방입니다.")
            session['player_name'] = name
            session['room_id'] = room_id
            broadcast_lobby_state(room_id)
            # 정원 충족 시 게임 시작 알림
            if room_id in games:
                socketio.emit("lobby_start", {"room": room_id}, room=room_id)
            return redirect(url_for('index'))
        # GET 요청 → 로비 화면으로 돌려보내고, 로비에서 이름 입력 폼을 표시
        return redirect(url_for('lobby', room_id=room_id))

    @app.route('/')
    def index():
        # 0) 아직 어떤 방도 선택하지 않은 사용자는 공개 로비로 이동
        if 'room_id' not in session:
            return redirect(url_for('rooms'))

        # 1) 방은 선택했으나 아직 Game 인스턴스가 만들어지지 않은 경우 → 로비로
        rid = session['room_id']
        if rid in waiting_rooms:
            return redirect(url_for('lobby', room_id=rid))

        # 2) 실제 게임 객체가 존재해야 정상 진행
        game = current_game()
        # 게임이 인원 부족으로 중단(aborted)된 경우 → 방 로비로 이동
        if game and getattr(game, "aborted", False):
            return redirect(url_for('lobby', room_id=rid))
        if not game:
            # 방이 사라졌거나 리셋된 상황 → 로비로 보내고 세션 초기화
            session.pop('room_id', None)
            return redirect(url_for('rooms'))
        if 'player_name' not in session:
            return redirect(url_for('join', room_id=session.get('room_id')))
        player_name = session['player_name']
        if game.game_over:
            final_scores = {p.name: p.total_points for p in game.players}
            return render_template("main.html", 
                                game_over=True,
                                final_scores=final_scores,
                                player_name=player_name,
                                pending_j=session.get("pending_j", False))
        if len(game.players) < game.cfg.player_count:
            state = {"message": "아직 플레이어가 모이지 않았습니다.",
                    "players": [p.name for p in game.players]}
            return render_template("waiting.html", state=state, player_count=game.cfg.player_count)
        if game.current_round is None:
            game.start_round()
            broadcast_game_state(game)
        return render_template("main.html", player_name=player_name, game_over=False, pending_j=session.get("pending_j", False))

    @app.route('/action', methods=['POST'])
    def action():
        if 'player_name' not in session:
            return redirect(url_for('join', room_id=session.get('room_id')))
        game = current_game()
        if not game:
            return redirect(url_for('index'))
        player_name = session['player_name']
        player = next((p for p in game.players if p.name == player_name), None)
        if game.current_round is None:
            return redirect(url_for('index'))
        cur_round = game.current_round
        current_turn = cur_round.players[cur_round.current_turn_index].name
        if player.name != current_turn:
            flash_error("아직 당신의 차례가 아닙니다.")
            broadcast_game_state(game)
            return ('', 204)
        
        action_type = request.form.get("action_type")
        if action_type == "raise":
            indices = request.form.getlist("card_indices")
            try:
                indices = [int(x) for x in indices]
            except ValueError:
                flash_error("유효한 카드 인덱스가 아닙니다.")
                broadcast_game_state(game)
                return ('', 204)
            success, message = cur_round.player_raise(player, indices)
            if not success:
                flash_error(message)
                broadcast_game_state(game)
                return ('', 204)
            if cur_round.bet_history[-1][3] == "J":
                # J 효과 대기 상태 등록
                game.j_pending[player.id] = True
                broadcast_game_state(game)
                return ('', 204)
        elif action_type == "fold":
            # If eligible for mulligan
            if not player.has_raised and not player.mulligan_used:
                new_cards = game.deck.draw(game.cfg.mulligan_count)
                player.hand.extend(new_cards)
                game.mulligan_info[player.id] = {
                    'pending': True,
                    'cards': [str(app.jinja_env.globals['card_to_html'](c)) for c in new_cards]
                }
                broadcast_game_state(game)
                return ('', 204)   # no full-page reload; updates via SocketIO
            # Otherwise perform fold
            success, message = cur_round.player_fold(player)
            if not success:
                flash_error(message)
                broadcast_game_state(game)
                return ('', 204)
        elif action_type == 'mulligan_discard':
            indices = request.form.getlist('discard_indices')
            indices = sorted([int(x) for x in indices], reverse=True)
            if len(indices) != game.cfg.mulligan_count:
                flash_error(f"{game.cfg.mulligan_count}장을 정확히 선택해야 합니다.")
                broadcast_game_state(game)
                return ('', 204)
            for idx in indices:
                try:
                    player.hand.pop(idx)
                except IndexError:
                    flash_error("잘못된 카드 인덱스입니다.")
                    broadcast_game_state(game)
                    return ('', 204)
            player.mulligan_used = True
            player.last_action = "멀리건"
            game.mulligan_info.pop(player.id, None)   # clear
            success, message = cur_round.player_fold(player)
            if not success:
                flash_error(message)
                broadcast_game_state(game)
                return ('', 204)
            
        elif action_type == 'j_select':
            target = request.form.get('target')
            if not target:
                flash_error("유효한 대상을 선택해주세요.")
                broadcast_game_state(game)
                return ('', 204)
            # store hand HTML for selected target
            target_player = next(p for p in game.players if p.name == target)
            session['j_target'] = target
            session['j_target_hand'] = [str(app.jinja_env.globals['card_to_html'](c)) for c in target_player.hand]
            broadcast_game_state(game)
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
            game.j_pending.pop(player.id, None)
            # broadcast info message to everyone
            game.info_message = f"{player_name} ▶ {target_name} 의 {card_html} 를 버렸습니다."
        else:
            flash_error("알 수 없는 행동입니다.")
            broadcast_game_state(game)
            return ('', 204)
        if cur_round.check_round_over():
            game.end_round()
        else:
            cur_round.next_player()
        broadcast_game_state(game)
        return redirect(url_for('index'))

    @app.route('/logout')
    def logout():
        rid  = session.get('room_id')
        name = session.get('player_name')
        session.clear()

        # ── 진행 중 게임에서 이탈 ──────────────────────────────
        if rid and rid in games:
            game = games[rid]
            aborted = game.remove_player(name)     # returns True if players < min
            if aborted:
                # Move remaining players to waiting room
                waiting_rooms[rid] = {
                    'config': game.cfg,
                    'joiners': [p.name for p in game.players]
                }
                games.pop(rid, None)
                broadcast_lobby_state(rid)
                broadcast_lobby_restart(rid)
                # If no players remain in waiting room, delete it
                if rid in waiting_rooms and len(waiting_rooms[rid]['joiners']) == 0:
                    waiting_rooms.pop(rid, None)
            else:
                broadcast_game_state(game)

        # ── 대기실에서 이탈 ────────────────────────────────────
        elif rid and rid in waiting_rooms:
            wr = waiting_rooms[rid]
            if name in wr['joiners']:
                wr['joiners'].remove(name)
                broadcast_lobby_state(rid)
                if len(wr['joiners']) == 0:
                    waiting_rooms.pop(rid, None)

        return redirect(url_for('rooms'))

    @app.route('/reset')
    def reset():
        rid = session.get('room_id')
        if rid and rid in games:
            games.pop(rid)
        if rid and rid in waiting_rooms:
            waiting_rooms.pop(rid)
        session.clear()
        # No game here to broadcast; skip broadcast_game_state
        return redirect(url_for('join', room_id=rid) if rid else url_for('rooms'))

    @socketio.on('connect')
    def handle_connect():
        player_name = session.get('player_name')
        if player_name:
            g = current_game()
            if g:
                player_obj = next((pl for pl in g.players if pl.name == player_name), None)
                if player_obj:
                    join_room(player_room(g, player_obj))
                    # Join the room‑wide channel as well for lobby events
                    join_room(g.room_id)
                    emit('game_state_update', get_state_for(g, player_name), room=player_room(g, player_obj))

    @socketio.on("join_lobby")
    def handle_join_lobby(data):
        join_room(data.get("room"))