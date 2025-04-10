from flask import Flask
from flask_socketio import SocketIO

def create_app():
    app = Flask(__name__)
    app.secret_key = 'super-secret-key'
    socketio = SocketIO(app, cors_allowed_origins="*")

    from models import card_to_html
    app.jinja_env.globals.update(card_to_html=card_to_html)
    app.jinja_env.globals.update(enumerate=enumerate)
    
    from routes import register_routes
    register_routes(app, socketio)  # routes.py에 정의된 라우트들을 등록

    return app, socketio



if __name__ == "__main__":
    app, socketio = create_app()
    socketio.run(app, debug=True, host="0.0.0.0", port=5001)