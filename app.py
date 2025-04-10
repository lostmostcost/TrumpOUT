# app.py
from flask import Flask, render_template_string, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
import random, os

# 밸런스 조정용 변수들
PLAYER_COUNT = 4         # 참여 플레이어 수
HAND_COUNT = 5             # 초기 패의 장수
MULLIGAN_COUNT = 3         # 멀리건 시 추가로 뽑는 카드 장수
FIRST_PLAYER_THRESHOLD = 2 # 각 플레이어가 선플레이어한 횟수가 이 수치를 넘으면 게임 종료
MAX_CARDS_PER_PLAY = 2     # 한 번에 낼 수 있는 숫자 카드 최대 장수

app = Flask(__name__)
app.secret_key = 'super-secret-key'
app.jinja_env.globals.update(enumerate=enumerate)

def card_to_html(card):
    """Card 객체를 기호와 색상으로 표시하는 HTML 문자열 반환"""
    if card.is_special():
        return f'<span style="color: purple; font-weight: bold;">{card.rank.upper()}</span>'
    else:
        symbol_map = {"Hearts": "♥", "Diamonds": "♦", "Clubs": "♣", "Spades": "♠"}
        symbol = symbol_map.get(card.suit, "")
        color = "red" if card.suit in ["Hearts", "Diamonds"] else "black"
        return f'<span style="color: {color};">{card.rank}{symbol}</span>'

app.jinja_env.globals.update(card_to_html=card_to_html)

socketio = SocketIO(app, cors_allowed_origins="*")

###################################
# 오류 응답 함수
###################################
def error_response(message):
    return f'''
    <html>
      <body>
        <p>{message}</p>
        <button onclick="history.back()">이전 화면으로 돌아가기</button>
      </body>
    </html>
    '''

###################################
# 전역 변수: revealed_hands (j 카드 효과 결과 저장)
###################################
revealed_hands = {}

###################################
# 템플릿 정의
###################################
JOIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>게임 참가</title>
</head>
<body>
  <h1>TrumpOUT 게임 참가</h1>
  <form method="POST" action="/join">
    <p>플레이어 이름: <input type="text" name="name"></p>
    <button type="submit">참가</button>
  </form>
  <p>현재 참가자: {{ players }}</p>
  <p>최대 {{ player_count }}명까지 참여할 수 있습니다.</p>
</body>
</html>
"""

WAITING_PAGE_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>플레이어 대기중</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.1/socket.io.min.js"></script>
  <script type="text/javascript">
      var socket = io();
      socket.on('game_state_update', function(data) {
          if (data.players) {
              // ID가 players인 요소에 최신 플레이어 목록을 표시
              document.getElementById("players").innerHTML = data.players.join(", ");
              // 만약 플레이어 수가 최대치 이상이라면 페이지를 자동으로 리다이렉트
              if (data.players.length >= {{ player_count }}) {
                  window.location.href = "/";
              }
          }
      });
  </script>
</head>
<body>
  <h1>플레이어 대기중</h1>
  <p id="message">{{ state.message }}</p>
  <p>현재 참가자: <span id="players">{{ state.players | join(", ") }}</span></p>
  <br>
  <a href="/logout">나가기</a>
</body>
</html>
"""

MAIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>TrumpOUT</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.1/socket.io.min.js"></script>
  <script type="text/javascript">
      var socket = io();
      socket.on('connect', function() {
          console.log('서버와 연결되었습니다.');
      });
      socket.on('game_state_update', function(data) {
    if (data.round_over) {
        // 라운드 종료 시 500ms 후 전체 페이지를 새로고침하여 클라이언트 상태를 갱신합니다.
        setTimeout(function() { location.reload(); }, 500);
        return;
    }
    
    // 배팅 내역 업데이트
    var betHistoryContainer = document.getElementById("betHistory");
    if (betHistoryContainer) {
        var betHistoryHtml = "";
        if (data.bet_history && data.bet_history.length > 0) {
            data.bet_history.forEach(function(entry) {
                betHistoryHtml += "<div class='bet-entry'>";
                betHistoryHtml += "<div class='bet-header'><strong>" + entry[0] + "</strong>님의 배팅 - [합: " + entry[2] + "]</div>";
                betHistoryHtml += "<div>사용한 카드:<br>" + entry[1].join("<br>") + "</div>";
                betHistoryHtml += "</div>";
            });
        } else {
            betHistoryHtml = "<p>배팅 내역이 없습니다.</p>";
        }
        betHistoryContainer.innerHTML = betHistoryHtml;
    }
    
    // 현재 라운드 선플레이어 업데이트
    var firstPlayerContainer = document.getElementById("firstPlayer");
    if (firstPlayerContainer) {
        firstPlayerContainer.innerHTML = "현재 라운드 선플레이어: " + data.first_player;
    }
    
    // 턴 진행 순서 업데이트
    var turnOrderContainer = document.getElementById("turnOrder");
    if (turnOrderContainer) {
        if (data.turn_order && data.current_turn) {
            var turnOrderHtml = "턴 진행 순서: ";
            data.turn_order.forEach(function(name, index) {
                if (name === data.current_turn) {
                    turnOrderHtml += "<span class='current'>" + name + "</span>";
                } else {
                    turnOrderHtml += name;
                }
                if (index != data.turn_order.length - 1) {
                    turnOrderHtml += " → ";
                }
            });
            turnOrderContainer.innerHTML = turnOrderHtml;
        } else {
            turnOrderContainer.innerHTML = "턴 진행 순서를 불러올 수 없습니다.";
        }
    }
    
    // 실시간 플레이어 현황 업데이트 (테이블 본문)
    var playerStatusContainer = document.getElementById("playerStatus");
    if (playerStatusContainer) {
        var playerStatusHtml = "";
        if (data.players) {
            for (var playerName in data.players) {
                var status = data.players[playerName];
                playerStatusHtml += "<tr>";
                playerStatusHtml += "<td>" + playerName + "</td>";
                playerStatusHtml += "<td>" + status['행동'] + "</td>";
                playerStatusHtml += "<td>" + status['남은 패'] + "</td>";
                playerStatusHtml += "<td>" + status['누적 승점'] + "</td>";
                playerStatusHtml += "<td>" + status['선플레이어 횟수'] + "</td>";
                playerStatusHtml += "<td>" + (status['score_cards'] ? status['score_cards'].join(\", \") : "") + "</td>";
                playerStatusHtml += "</tr>";
            }
        } else {
            playerStatusHtml = "<tr><td colspan='6'>플레이어 정보를 불러올 수 없습니다.</td></tr>";
        }
        playerStatusContainer.innerHTML = playerStatusHtml;
    }
});
  </script>
  <style>
    .current { font-weight: bold; color: blue; }
    .bet-entry { margin-bottom: 10px; padding: 5px; border-bottom: 1px solid #ccc; }
    .bet-header { font-weight: bold; }
    .container { display: flex; }
    .left { flex: 2; padding: 10px; }
    .right { flex: 1; padding: 10px; border-left: 2px solid #ccc; }
  </style>
</head>
<body>
  <h1>TrumpOUT 게임</h1>
  <p>플레이어: {{ player_name }}</p>
  {% if game_over %}
      <h2>게임 종료</h2>
      <p>최종 점수:</p>
      <ul>
        {% for name, score in final_scores.items() %}
          <li>{{ name }}: {{ score }}</li>
        {% endfor %}
      </ul>
      <br><a href="/reset">게임 초기화</a>
  {% else %}
    <div class="container">
      <div class="left">
        <h3>내 상태</h3>
        <p>남은 패: {{ hand_count }} 장</p>
        <h3>내 손패 및 선택</h3>
        <form method="POST" action="/action">
            <input type="hidden" name="action_type" value="raise">
            <ul id="handContainer">
                {% for i, card in enumerate(hand) %}
                    <li>
                        <label>
                            <input type="checkbox" name="card_indices" value="{{ i }}">
                            {{ card_to_html(card)|safe }}
                        </label>
                    </li>
                {% endfor %}
            </ul>
            <button type="submit">배팅 (Raise)</button>
        </form>
        <br>
        <form method="POST" action="/action">
          <input type="hidden" name="action_type" value="fold">
          <button type="submit">폴드 (Fold)</button>
        </form>
        <h3>배팅 내역 (최신 순)</h3>
        {% if bet_history %} 
          <ul id="betHistory">
            {% for entry in bet_history %}
              <li class="bet-entry">
                <div class="bet-header">
                  <strong>{{ entry[0] }}</strong>님의 배팅 - [합: {{ entry[2] }}]
                </div>
                <div>
                  사용한 카드:<br>
                  {% for card in entry[1] %}
                    {{ card|safe }}{% if not loop.last %}<br>{% endif %}
                  {% endfor %}
                </div>
              </li>
            {% endfor %}
          </ul>
        {% else %}
          <p id="betHistory">배팅 내역이 없습니다.</p>
        {% endif %}
      </div>
      <div class="right">
         <h3 id="firstPlayer">현재 라운드 선플레이어: {{ first_player }}</h3>
         <h3>턴 진행 순서:</h3>
         <p id="turnOrder">
          {% for name in turn_order %}
             {% if name == current_turn %}
               <span class="current">{{ name }}</span>
             {% else %}
               {{ name }}
             {% endif %}
             {% if not loop.last %} → {% endif %}
          {% endfor %}
         </p>
         {% if pending_j %}
           <p><a href="/j_select">J 카드 효과 사용: 대상 플레이어 선택</a></p>
         {% endif %}
         <h3>실시간 플레이어 현황</h3>
         <table border="1" cellpadding="5" cellspacing="0">
           <tr>
             <th>플레이어</th>
             <th>최근 행동</th>
             <th>남은 패</th>
             <th>누적 승점</th>
             <th>선플레이어 횟수</th>
             <th>획득 카드</th>
           </tr>
           <tbody id="playerStatus">
           {% for ps in player_status %}
           <tr>
             <td>{{ ps['이름'] }}</td>
             <td>{{ ps['행동'] }}</td>
             <td>{{ ps['남은 패'] }}</td>
             <td>{{ ps['누적 승점'] }}</td>
             <td>{{ ps['선플레이어 횟수'] }}</td>
             <td>
               {% for card in ps['score_cards'] %}
                 {{ card|safe }}{% if not loop.last %}, {% endif %}
               {% endfor %}
             </td>
           </tr>
           {% endfor %}
           </tbody>
         </table>
      </div>
    </div>
  {% endif %}
  <br>
  <form method="GET" action="/logout">
      <button type="submit">나가기</button>
  </form>
  <div id="gameState" style="display: none;"></div>
</body>
</html>
"""

###################################
# 클래스 정의 (특수 카드 기능 추가 및 카드 순환 관리)
###################################
class Card:
    def __init__(self, suit, rank):
        self.suit = suit  # 숫자 카드: "Hearts", "Diamonds", "Clubs", "Spades"
        self.rank = rank  # 숫자: "1"~"10" 또는 특수: "j", "q", "k", "joker"

    def __repr__(self):
        if self.is_special():
            return f"{self.rank.upper()}"
        if self.suit:
            return f"{self.rank} of {self.suit}"
        return f"{self.rank}"

    def is_special(self):
        return self.rank.lower() in ["j", "q", "k", "joker"]

    def numeric_value(self):
        if not self.is_special():
            return int(self.rank)
        return None

class Deck:
    def __init__(self):
        self.cards = []
        self._build_full_deck()
        random.shuffle(self.cards)

    def _build_full_deck(self):
        self.cards = []
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        for suit in suits:
            for rank in map(str, range(1, 11)):
                self.cards.append(Card(suit, rank))
        for rank in ["j", "q", "k"]:
            for _ in range(2):  # 현재 코드에서는 2장씩 사용
                self.cards.append(Card(None, rank))
        self.cards.append(Card(None, "joker"))
    
    def refresh_deck(self, game):
        full = {}
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        # 전체 덱 구성
        for suit in suits:
            for rank in map(str, range(1, 11)):
                full[(suit, rank)] = full.get((suit, rank), 0) + 1
        for rank in ["j", "q", "k"]:
            full[(None, rank)] = 2
        full[(None, "joker")] = 1

        in_play = {}
        def add_card(card):
            key = (card.suit, card.rank)
            in_play[key] = in_play.get(key, 0) + 1

        for p in game.players:
            for card in p.hand:
                add_card(card)
            for card in p.score_cards:
                add_card(card)
        # 여기서 현재 진행 중인 라운드의 bet_history를 참조합니다.
        if game.current_round is not None:
            for entry in game.current_round.bet_history:
                for card in entry[1]:
                    add_card(card)
                    
        remaining = []
        for key, count in full.items():
            remain = count - in_play.get(key, 0)
            for _ in range(remain):
                remaining.append(Card(key[0], key[1]))
        random.shuffle(remaining)
        self.cards = remaining

    def draw(self, n=1):
        drawn = []
        for _ in range(n):
            if len(self.cards) < 1:
                self.refresh_deck(game)
                if len(self.cards) < 1:
                    break
            drawn.append(self.cards.pop())
        return drawn

    def add_cards(self, cards):
        self.cards.extend(cards)
        random.shuffle(self.cards)

class Player:
    def __init__(self, name):
        self.name = name
        self.hand = []              
        self.score_cards = []       
        self.total_points = 0
        self.current_bet_cards = []
        self.in_round = True
        self.starting_count = 0
        self.has_raised = False     
        self.mulligan_used = False  
        self.last_action = "대기"

    def draw_to_handcount(self, deck):
        while len(self.hand) < HAND_COUNT:
            new_cards = deck.draw(1)
            if not new_cards:
                break
            self.hand.extend(new_cards)

class Round:
    def __init__(self, players, deck, first_player_index):
        self.players = players
        self.deck = deck
        self.first_player_index = first_player_index
        self.current_turn_index = first_player_index
        self.current_highest = 0
        self.bet_history = []  # (플레이어 이름, [Card, ...], 최종 계산 점수, 특수 효과)
        self.active_players = {p.name: p for p in players}
        self.round_over = False
        self.winner = None
        self.reversed = False

    def next_player(self):
        n = len(self.players)
        for _ in range(n):
            self.current_turn_index = (self.current_turn_index + 1) % n
            cur = self.players[self.current_turn_index]
            if cur.name in self.active_players:
                return cur
        return None

    def player_raise(self, player, selected_card_indices, target_player=None):
        if not selected_card_indices:
            return False, "최소 한 장 이상의 카드를 선택해야 합니다."
        try:
            selected_cards = [player.hand[i] for i in selected_card_indices]
        except IndexError:
            return False, "잘못된 카드 인덱스입니다."
        special_cards = [card for card in selected_cards if card.is_special()]
        numeric_cards = [card for card in selected_cards if not card.is_special()]
        if len(special_cards) > 1:
            return False, "한 번에 특수 카드는 최대 1장만 사용할 수 있습니다."
        allowed = MAX_CARDS_PER_PLAY if not special_cards else MAX_CARDS_PER_PLAY + 1
        if len(selected_cards) > allowed:
            return False, f"한 번에 낼 수 있는 전체 카드 수는 최대 {allowed}장입니다."
        if special_cards and not numeric_cards:
            return False, "특수 카드는 반드시 숫자 카드와 함께 제출해야 합니다."
        base_sum = sum(card.numeric_value() for card in numeric_cards)
        effective_value = base_sum
        special_effect = None
        new_reversed = self.reversed

        if special_cards:
            sp = special_cards[0]
            rank = sp.rank.lower()
            if rank == "j":
                special_effect = "J"
                session['pending_j'] = True
            elif rank == "q":
                special_effect = "Q"
                new_reversed = not self.reversed
            elif rank == "k":
                special_effect = "K"
                # 수정: 정방향이면 base_sum에 5를 더하고, 역방향이면 5를 뺍니다.
                if not self.reversed:
                    effective_value = base_sum + 5
                else:
                    effective_value = base_sum - 5
            elif rank == "joker":
                special_effect = "joker"
                if not self.reversed:
                    effective_value = base_sum + self.current_highest
                else:
                    effective_value = base_sum - self.current_highest

        if not new_reversed:
            if self.current_highest != 0 and effective_value <= self.current_highest:
                return False, f"제출한 숫자({effective_value})는 현재 최고({self.current_highest})보다 커야 합니다."
        else:
            if self.current_highest != 0 and effective_value >= self.current_highest:
                return False, f"(q 효과) 제출한 숫자({effective_value})는 현재 최고({self.current_highest})보다 작아야 합니다."
        # joker 효과에 대해 score_sum은 숫자 카드의 합(base_sum)만 인정합니다.
        score_sum = base_sum if special_effect == "joker" else effective_value
        self.bet_history.append((player.name, selected_cards, score_sum, special_effect))
        self.current_highest = effective_value
        self.reversed = new_reversed
        player.current_bet_cards = selected_cards
        player.has_raised = True
        player.last_action = "배팅"
        selected_set = set(selected_card_indices)
        player.hand = [card for i, card in enumerate(player.hand) if i not in selected_set]
        return True, "raise 제출 완료."

    def player_fold(self, player):
        if player.name in self.active_players:
            del self.active_players[player.name]
        player.last_action = "폴드"
        return True, "폴드 처리 완료."

    def check_round_over(self):
        # active_players가 0이면 라운드 종료
        if len(self.active_players) == 0:
            self.round_over = True
            return True
        # active_players가 1명인 경우
        if len(self.active_players) == 1:
            remaining_player = list(self.active_players.values())[0]
            # 남은 플레이어가 이미 행동(배팅)이 끝난 경우라면 라운드를 종료,
            # 아직 raise하지 않았다면 아직 턴을 진행할 수 있으므로 종료하지 않음.
            if remaining_player.has_raised:
                self.round_over = True
                self.winner = remaining_player
                return True
            else:
                return False
        # 그 외의 경우(활성 플레이어 2명 이상)에는 라운드를 종료하지 않음.
        return False

    def finish_round(self):
        if not self.winner:
            return
        winner_bets = [entry for entry in self.bet_history if entry[0] == self.winner.name]
        if not winner_bets:
            return
        _, cards, num_sum, special_effect = winner_bets[-1]
        self.winner.total_points += num_sum
        # 승리자의 제출 카드 중, 숫자 카드와 k 카드는 점수 카드로 획득
        score_cards_to_add = []
        for card in cards:
            if not card.is_special():
                score_cards_to_add.append(card)
            elif card.is_special() and card.rank.lower() == "k":
                score_cards_to_add.append(card)
        self.winner.score_cards.extend(score_cards_to_add)
        # 다른 카드들은 덱으로 돌려보냅니다.
        returned_cards = []
        for entry in self.bet_history:
            if entry[0] != self.winner.name:
                returned_cards.extend(entry[1])
            else:
                # 승리 플레이어의 배팅 내역 중 점수 카드로 사용된 카드들은 제외하고 나머지(특수 카드 j, q, joker 등)는 반환
                for card in entry[1]:
                    if card.is_special() and card.rank.lower() != "k":
                        returned_cards.append(card)
        self.deck.add_cards(returned_cards)
        self.winner.draw_to_handcount(self.deck)

class Game:
    def __init__(self):
        self.deck = Deck()
        self.players = []
        self.current_round = None
        self.first_player_index = None
        self.round_history = []
        self.game_over = False

    def add_player(self, player):
        if len(self.players) >= PLAYER_COUNT:
            return False
        self.players.append(player)
        return True

    def start_round(self):
        for player in self.players:
            player.draw_to_handcount(self.deck)
            player.current_bet_cards = []
            player.in_round = True
            player.has_raised = False
            player.mulligan_used = False
            player.last_action = "대기"
        if self.first_player_index is None:
            self.first_player_index = random.randint(0, len(self.players)-1)
        starting_player = self.players[self.first_player_index]
        starting_player.starting_count += 1
        self.current_round = Round(self.players, self.deck, self.first_player_index)
        return self.current_round

    def end_round(self):
        if self.current_round:
            self.current_round.finish_round()
            self.round_history.append({
                "winner": self.current_round.winner.name if self.current_round.winner else None,
                "highest": self.current_round.current_highest
            })
            self.first_player_index = (self.first_player_index + 1) % len(self.players)
            self.current_round = None
            if all(p.starting_count >= FIRST_PLAYER_THRESHOLD for p in self.players):
                self.game_over = True

# 전역 게임 인스턴스
game = Game()

def get_current_game_state():
    if game.current_round:
        bet_history_serialized = []
        for entry in game.current_round.bet_history[::-1]:
            player_name, cards, num_sum, special_effect = entry
            bet_history_serialized.append((player_name, [str(card_to_html(c)) for c in cards], num_sum))
        
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
            "players": {p.name: {"남은 패": len(p.hand),
                                 "누적 승점": p.total_points,
                                 "선플레이어 횟수": p.starting_count,
                                 "행동": p.last_action,
                                 "score_cards": [card_to_html(c) for c in p.score_cards]}
                        for p in game.players},
            "pending_j": session.get("pending_j", False),
            "round_over": False
        }
        
        # 추가: 현재 플레이어의 손패를 HTML로 변환하여 'my_hand' 필드에 저장
        current_player_name = session.get("player_name")
        my_hand = []
        if current_player_name:
            player_obj = next((p for p in game.players if p.name == current_player_name), None)
            if player_obj:
                for i, card in enumerate(player_obj.hand):
                    hand_item = "<li><label><input type='checkbox' name='card_indices' value='{0}'> {1}</label></li>".format(i, card_to_html(card))
                    my_hand.append(hand_item)
        state["my_hand"] = my_hand
        
    else:
        state = {"message": "라운드가 종료되었습니다.", "players": [p.name for p in game.players]}
        state["round_over"] = True if game.round_history else False
    return state

def broadcast_game_state():
    state = get_current_game_state()
    socketio.emit('game_state_update', state)

###################################
# 신규 라우트: j 카드 대상 선택 및 보기
###################################
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
            revealed_hands[current_player] = (target, [card_to_html(c) for c in target_player.hand])
        session.pop("pending_j", None)
        return redirect(url_for('j_view'))
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
      <meta charset="UTF-8">
      <title>J 카드 효과 - 대상 선택</title>
    </head>
    <body>
      <h1>J 카드 효과: 대상 플레이어 선택</h1>
      <form method="POST">
        <p>대상 플레이어:
          <select name="target">
            {% for name in candidates %}
              <option value="{{ name }}">{{ name }}</option>
            {% endfor %}
          </select>
        </p>
        <button type="submit">선택</button>
      </form>
      <p><a href="/">메인 페이지로 돌아가기</a></p>
    </body>
    </html>
    """, candidates=candidate_names)

@app.route('/j_view')
def j_view():
    if 'player_name' not in session:
        return redirect(url_for('join'))
    current_player = session['player_name']
    target_info = revealed_hands.get(current_player)
    if not target_info:
        return error_response("J 카드 효과로 공개된 대상이 없습니다.")
    target_name, hand_html_list = target_info
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
      <meta charset="UTF-8">
      <title>J 카드 효과 - {{ target_name }}의 손패</title>
    </head>
    <body>
      <h1>{{ target_name }}의 현재 패</h1>
      <p>
        {% for card in hand %}
          {{ card|safe }} {% endfor %}
      </p>
      <p><a href="/">닫기</a></p>
    </body>
    </html>
    """, target_name=target_name, hand=hand_html_list)

###################################
# 라우트 정의
###################################
@app.route('/')
def index():
    if 'player_name' not in session:
        return redirect(url_for('join'))
    player_name = session['player_name']
    if game.game_over:
        final_scores = {p.name: p.total_points for p in game.players}
        return render_template_string(MAIN_PAGE_HTML,
                                      game_over=True,
                                      final_scores=final_scores,
                                      player_name=player_name,
                                      pending_j=session.get("pending_j", False))
    if len(game.players) < PLAYER_COUNT:
        state = {"message": "아직 플레이어가 모이지 않았습니다.",
                "players": [p.name for p in game.players]}
        return render_template_string(WAITING_PAGE_HTML, state=state, player_count=PLAYER_COUNT)
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
            "score_cards": [card_to_html(c) for c in p.score_cards]
        })
    
    return render_template_string(MAIN_PAGE_HTML,
                                  game_over=False,
                                  player_name=player_name,
                                  first_player=cur_round.players[cur_round.first_player_index].name,
                                  hand_count=len(player.hand),
                                  hand=player.hand,
                                  score_cards=player.score_cards,
                                  total_points=player.total_points,
                                  bet_history=[(entry[0], [card_to_html(c) for c in entry[1]], entry[2])
                                               for entry in cur_round.bet_history[::-1]],
                                  my_turn=my_turn,
                                  round_history=game.round_history,
                                  turn_order=turn_order,
                                  current_turn=current_turn,
                                  final_scores={},
                                  player_status=player_status,
                                  pending_j=session.get("pending_j", False))

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
    return render_template_string(JOIN_PAGE_HTML, players=current_players, player_count=PLAYER_COUNT)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('join'))

@app.route('/reset')
def reset():
    global game, revealed_hands
    game = Game()
    revealed_hands = {}
    session.clear()
    broadcast_game_state()
    return redirect(url_for('join'))

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
        mulligan_html = """
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <title>멀리건 선택</title>
        </head>
        <body>
            <h1>멀리건 (Mulligan)</h1>
            <p>추가 {{ mulligan_count }}장의 카드를 드로우하여 손패가 {{ total_cards }}장이 되었습니다.<br>
            아래에서 교체할 정확히 {{ mulligan_count }}장의 카드를 선택하세요.<br>
            (선택 후 자동으로 폴드되어 턴이 종료됩니다.)</p>
            <form method="POST">
                {% for i, card in enumerate(hand) %}
                    <label>
                        <input type="checkbox" name="discard_indices" value="{{ i }}">
                        {{ card_to_html(card)|safe }}
                    </label><br>
                {% endfor %}
                <button type="submit">확인 및 자동 폴드</button>
            </form>
        </body>
        </html>
        """
        return render_template_string(mulligan_html, hand=player.hand, mulligan_count=MULLIGAN_COUNT, total_cards=total_cards)

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

@app.route('/game_state')
def game_state():
    return jsonify(get_current_game_state())

@socketio.on('connect')
def handle_connect():
    emit('game_state_update', get_current_game_state())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, debug=True, host="0.0.0.0", port=port)