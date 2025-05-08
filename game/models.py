from flask import session
import random
from dataclasses import dataclass
import uuid

@dataclass
class GameConfig:
    title: str                    = "Trump OUT Room"
    private: bool                 = False
    player_count: int             = 4
    hand_count: int               = 6
    mulligan_count: int           = 4
    max_cards_per_play: int       = 2
    first_player_threshold: int   = 5

def card_to_html(card):
    """Card 객체를 기호와 색상으로 표시하는 HTML 문자열 반환"""
    symbol_map = {
        "Hearts": "♥", "Diamonds": "♦",
        "Clubs": "♣", "Spades": "♠",
        "Black": "🃏", "Color": "🃏"
    }
    symbol = symbol_map.get(card.suit, "")
    if card.rank.lower() == "joker":
        if card.suit == "Black":
            color = "black"
        elif card.suit == "Color":
            color = "orange"
    else:
        color = "red" if card.suit in ["Hearts", "Diamonds"] else "black"
    # Add tooltip for special cards
    tooltip = ""
    if card.is_special():
        rank = card.rank.lower()
        if rank == "j":
            tooltip = "플레이어를 선택해 패를 보고 한 장 버리기"
        elif rank == "q":
            tooltip = "레이즈 방향 역전"
        elif rank == "k":
            tooltip = "함께 내는 숫자에 +5 (역방향일 때 -5)"
        elif rank == "joker":
            tooltip = "마지막 플레이어 레이즈 값 카피 (점수 계산 X)"
    if tooltip:
        return f'<span title="{tooltip}" style="color: {color};">{card.rank.upper()}{symbol}</span>'
    return f'<span style="color: {color};">{card.rank.upper()}{symbol}</span>'

class Card:
    def __init__(self, suit, rank):
        self.suit = suit  # "Hearts", "Diamonds", "Clubs", "Spades"
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
    def __init__(self, game=None):
        self.game = game
        self.cards = []
        self._build_full_deck()
        random.shuffle(self.cards)

    def _build_full_deck(self):
        self.cards = []
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        for suit in suits:
            for rank in map(str, range(1, 11)):
                self.cards.append(Card(suit, rank))
        for suit in suits:
            for rank in ["j", "q", "k"]:
                self.cards.append(Card(suit, rank))
        self.cards.append(Card("Black", "joker"))
        self.cards.append(Card("Color", "joker"))
    
    def refresh_deck(self, game):
        full = {}
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        # 전체 덱 구성
        for suit in suits:
            for rank in map(str, range(1, 11)):
                full[(suit, rank)] = full.get((suit, rank), 0) + 1
        for suit in suits:
            for rank in ["j", "q", "k"]:
                full[(suit, rank)] = 1
        full[( "Black", "joker")] = 1
        full[( "Color", "joker")] = 1

        in_play = {}
        def add_card(card):
            key = (card.suit, card.rank)
            in_play[key] = in_play.get(key, 0) + 1

        for p in game.players:
            for card in p.hand:
                add_card(card)
            for card in p.score_cards:
                add_card(card)
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
            if len(self.cards) < 1 and self.game:
                self.refresh_deck(self.game)
                if len(self.cards) < 1:
                    break
            drawn.append(self.cards.pop())
        return drawn
class Player:
    def __init__(self, name: str, pid: str | None = None):
        # Unique identifier independent of display name
        self.id: str = pid or str(uuid.uuid4())
        self.name: str = name
        self.hand = []              
        self.score_cards = []       
        self.total_points = 0
        self.current_bet_cards = []
        self.in_round = True
        self.starting_count = 0
        self.has_raised = False     
        self.mulligan_used = False  
        self.last_action = "대기"

    def draw_to_handcount(self, deck, hand_count):
        while len(self.hand) < hand_count:
            new_cards = deck.draw(1)
            if not new_cards:
                break
            self.hand.extend(new_cards)

class Round:
    def __init__(self, players, deck, first_player_index, cfg):
        self.cfg = cfg
        self.players = players
        self.deck = deck
        self.first_player_index = first_player_index
        self.current_turn_index = first_player_index
        self.current_highest = 0
        self.bet_history = []  # (플레이어 이름, [Card, ...], 숫자 합, 특수 효과)
        # Use player.id as key to avoid name collision across games
        self.active_players = {p.id: p for p in players}
        self.round_over = False
        self.winner = None
        self.reversed = False

    def next_player(self):
        n = len(self.players)
        for _ in range(n):
            self.current_turn_index = (self.current_turn_index + 1) % n
            cur = self.players[self.current_turn_index]
            if cur.id in self.active_players:
                return cur
        return None

    def player_raise(self, player, selected_card_indices):
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
        allowed = self.cfg.max_cards_per_play if not special_cards else self.cfg.max_cards_per_play + 1
        if len(selected_cards) > allowed:
            return False, f"한 번에 낼 수 있는 전체 카드 수는 숫자 카드 {self.cfg.max_cards_per_play}장, 특수 카드 1장 입니다."
        if special_cards and not numeric_cards:
            return False, "특수 카드는 반드시 숫자 카드와 함께 제출해야 합니다."
        
        value = sum(card.numeric_value() for card in numeric_cards)
        special_effect = None
        new_reversed = self.reversed

        if special_cards:
            sp = special_cards[0]
            rank = sp.rank.lower()
            if rank == "q":
                special_effect = "Q"
                new_reversed = not self.reversed
            elif rank == "k":
                special_effect = "K"
                # 정방향이면 value에 5를 더하고, 역방향이면 5를 뺍니다.
                if not self.reversed:
                    value += 5
                else:
                    value -= 5
            elif rank == "joker":
                special_effect = "joker"
                if not self.reversed:
                    value += self.current_highest
                else:
                    value = self.current_highest - value

        if not new_reversed:
            if value <= self.current_highest:
                return False, f"제출한 숫자({value})는 현재 최고({self.current_highest})보다 커야 합니다."
        else:
            if value >= self.current_highest:
                return False, f"(q 효과) 제출한 숫자({value})는 현재 최고({self.current_highest})보다 작아야 합니다."
        
        # After passing the numeric comparison, handle J effect last
        if special_cards and special_cards[0].rank.lower() == "j":
            special_effect = "J"
            session['pending_j'] = True

        self.bet_history.append((player.name, selected_cards, value, special_effect))
        self.current_highest = value
        self.reversed = new_reversed
        player.current_bet_cards = selected_cards
        player.has_raised = True
        player.last_action = "배팅"
        selected_set = set(selected_card_indices)
        player.hand = [card for i, card in enumerate(player.hand) if i not in selected_set]
        return True, "raise 제출 완료."

    def player_fold(self, player):
        if player.id in self.active_players:
            del self.active_players[player.id]
        player.last_action = "폴드"
        return True, "fold 처리 완료."

    # ---- called when player leaves room mid‑round ------------------
    def remove_player(self, player):
        """Remove player from active tracking (e.g., left the game)."""
        # pop from active set
        self.active_players.pop(player.id, None)
        # also delete from players list to keep turn order sane
        self.players = [p for p in self.players if p.id != player.id]
        # adjust current_turn_index if needed
        if self.current_turn_index >= len(self.players):
            self.current_turn_index = 0

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
        # 승리 라운드의 마지막 배팅 항목을 가져와 unpack
        _, cards, value, special_effect = winner_bets[-1]
        # 카드들의 숫자 값만 합산하여 기본 점수 계산
        base_score = sum(card.numeric_value() for card in cards if card.numeric_value() is not None)
        ace_count = sum(1 for card in cards if not card.is_special() and card.rank == "1")
        ace_bonus = ace_count * 4
        total_score = base_score + ace_bonus
        if special_effect == 'K' :
            total_score += 5
        # 승리자에게 점수 부여
        self.winner.total_points += total_score
        # 승리자의 제출 카드 중, 숫자 카드와 k 카드는 점수 카드로 획득
        score_cards_to_add = []
        for card in cards:
            if not card.is_special():
                score_cards_to_add.append(card)
            elif card.is_special() and card.rank.lower() == "k":
                score_cards_to_add.append(card)
        self.winner.score_cards.extend(score_cards_to_add)
        # 라운드 종료 후 모든 플레이어 패를 지정된 hand_count 까지 보충
        for p in self.players:
            p.draw_to_handcount(self.deck, self.cfg.hand_count)

class Game:
    def __init__(self, cfg: GameConfig | None = None, room_id: str | None = None):
        self.cfg = cfg or GameConfig()
        self.room_id = room_id  
        self.deck = Deck(self)
        self.players = []
        self.current_round = None
        self.first_player_index = None
        self.round_history = []
        self.game_over = False
        # per‑game transient state
        self.j_pending: dict[str, bool] = {}          # player_name -> True/False
        self.mulligan_info: dict[str, dict] = {}      # player_name -> {'pending': bool, 'cards': [...]}
        self.info_message: str | None = None          # one‑shot broadcast message
        self.aborted = False     # True if game stopped because of insufficient players

    def add_player(self, player: "Player"):
        # Reject if room is full
        if len(self.players) >= self.cfg.player_count:
            return False
        # Reject if another player already has the same display name
        if any(p.name == player.name for p in self.players):
            return False
        self.players.append(player)
        return True

    def start_round(self):
        for player in self.players:
            player.draw_to_handcount(self.deck, self.cfg.hand_count)
            player.current_bet_cards = []
            player.in_round = True
            player.has_raised = False
            player.mulligan_used = False
            player.last_action = "대기"
        if self.first_player_index is None:
            self.first_player_index = random.randint(0, len(self.players)-1)
        starting_player = self.players[self.first_player_index]
        starting_player.starting_count += 1
        self.current_round = Round(self.players, self.deck, self.first_player_index, self.cfg)
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
            if all(p.starting_count >= self.cfg.first_player_threshold for p in self.players):
                self.game_over = True

    # ---- player leaves during an active game -----------------------
    def remove_player(self, player_name: str):
        """Called when a participant leaves; returns True if game was aborted."""
        player_obj = next((p for p in self.players if p.name == player_name), None)
        if not player_obj:
            return False
        # Remove from list
        self.players = [p for p in self.players if p.id != player_obj.id]
        # also from current round
        if self.current_round:
            self.current_round.remove_player(player_obj)
        # If now fewer than required players, abort game
        if len(self.players) < self.cfg.player_count:
            self.aborted = True
            self.game_over = True  # signal routers to stop
        return self.aborted