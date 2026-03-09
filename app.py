from flask import Flask, render_template, request, redirect, url_for, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import json
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chess-secret-key-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///chess.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Модели базы данных
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    
class Game(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    white_player_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    black_player_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    board_state = db.Column(db.Text, default='initial')
    current_turn = db.Column(db.String(5), default='white')
    status = db.Column(db.String(20), default='waiting')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Активные игры в памяти
active_games = {}
waiting_players = []

@app.route('/')
def index():
    if current_user.is_authenticated:
        return render_template('game_lobby.html')
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('Пользователь уже существует')
            return render_template('register.html')
        
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        return redirect(url_for('index'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Неверные данные')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/game/<game_id>')
@login_required
def game(game_id):
    game = Game.query.get_or_404(game_id)
    return render_template('game.html', game_id=game_id, game=game)

# Socket.IO события
@socketio.on('join_game')
def on_join_game(data):
    game_id = data['game_id']
    join_room(game_id)
    
    game = Game.query.get(game_id)
    if game:
        emit('game_state', {
            'board': get_initial_board() if game.board_state == 'initial' else json.loads(game.board_state),
            'turn': game.current_turn,
            'white_player': User.query.get(game.white_player_id).username if game.white_player_id else None,
            'black_player': User.query.get(game.black_player_id).username if game.black_player_id else None
        }, room=game_id)

@socketio.on('find_game')
def on_find_game(data):
    color_preference = data.get('color', 'random')
    
    # Ищем ожидающую игру
    waiting_game = Game.query.filter_by(status='waiting').first()
    
    if waiting_game and waiting_game.white_player_id != current_user.id:
        # Присоединяемся к существующей игре
        if not waiting_game.black_player_id:
            waiting_game.black_player_id = current_user.id
            waiting_game.status = 'active'
            db.session.commit()
            
            emit('game_found', {'game_id': waiting_game.id, 'color': 'black'})
            emit('game_found', {'game_id': waiting_game.id, 'color': 'white'}, 
                 room=f"user_{waiting_game.white_player_id}")
    else:
        # Создаем новую игру
        new_game = Game(
            white_player_id=current_user.id if color_preference != 'black' else None,
            black_player_id=current_user.id if color_preference == 'black' else None
        )
        db.session.add(new_game)
        db.session.commit()
        
        join_room(f"user_{current_user.id}")
        emit('waiting_for_opponent', {'game_id': new_game.id})

@socketio.on('make_move')
def on_make_move(data):
    game_id = data['game_id']
    move = data['move']
    
    game = Game.query.get(game_id)
    if game and game.status == 'active':
        # Проверяем, что ход делает правильный игрок
        player_color = 'white' if game.white_player_id == current_user.id else 'black'
        
        if player_color == game.current_turn:
            # Обновляем состояние игры
            game.current_turn = 'black' if game.current_turn == 'white' else 'white'
            db.session.commit()
            
            # Отправляем ход всем игрокам в комнате
            emit('move_made', {
                'move': move,
                'turn': game.current_turn
            }, room=game_id)

def get_initial_board():
    return [
        ['r', 'n', 'b', 'q', 'k', 'b', 'n', 'r'],
        ['p', 'p', 'p', 'p', 'p', 'p', 'p', 'p'],
        ['.', '.', '.', '.', '.', '.', '.', '.'],
        ['.', '.', '.', '.', '.', '.', '.', '.'],
        ['.', '.', '.', '.', '.', '.', '.', '.'],
        ['.', '.', '.', '.', '.', '.', '.', '.'],
        ['P', 'P', 'P', 'P', 'P', 'P', 'P', 'P'],
        ['R', 'N', 'B', 'Q', 'K', 'B', 'N', 'R']
    ]

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    import os
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port)