from . import create_app

app = create_app()

if __name__ == "__main__":
    from .config import get_settings

    s = get_settings()
    app.run(host=s.flask_host, port=s.flask_port, debug=(s.flask_env == "development"))
