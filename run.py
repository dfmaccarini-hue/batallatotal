from app import create_app

app = create_app()

if __name__ == "__main__":
    # Usar tus certificados mkcert para HTTPS local
    app.run(host="0.0.0.0", port=5001, ssl_context=("certs/192.168.33.48.pem", "certs/192.168.33.48-key.pem"))
