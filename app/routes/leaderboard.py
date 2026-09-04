import mercadopago
from flask import Blueprint, render_template, request, redirect, url_for
from app.models import Project, Bid
from app import db
from sqlalchemy import func

leaderboard_bp = Blueprint("leaderboard", __name__)

# Inicializar SDK con tu Access Token de pruebas
sdk = mercadopago.SDK("MP_TOKEN")  # reemplazá con tu token

@leaderboard_bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name = request.form["name"]
        description = request.form["description"]
        category = request.form["category"]
        initial_bid = request.form.get("initial_bid")

        project = Project(name=name, description=description, category=category)
        db.session.add(project)
        db.session.commit()

        if initial_bid and initial_bid.strip() != "":
            bid = Bid(amount=float(initial_bid), project_id=project.id)
            db.session.add(bid)
            db.session.commit()

        return redirect(url_for("leaderboard.index"))

    selected_category = request.args.get("category")

    query = (
        db.session.query(Project, func.max(Bid.amount).label("max_bid"))
        .outerjoin(Bid)
        .group_by(Project.id)
        .order_by(func.max(Bid.amount).desc())
    )

    if selected_category:
        query = query.filter(Project.category == selected_category)

    projects = query.all()

    categories = db.session.query(Project.category).distinct().all()
    categories = [c[0] for c in categories]

    return render_template(
        "leaderboard.html",
        projects=projects,
        categories=categories,
        selected_category=selected_category,
    )

@leaderboard_bp.route("/add_bid/<int:project_id>", methods=["POST"])
def add_bid(project_id):
    amount = float(request.form["amount"])

    # Guardar la puja
    bid = Bid(amount=amount, project_id=project_id)
    db.session.add(bid)
    db.session.commit()

    # Crear preferencia
    preference_data = {
        "items": [
            {
                "title": f"Puja Proyecto {project_id}",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": amount,
            }
        ]
    }

    preference_response = sdk.preference().create(preference_data)
    preference = preference_response["response"]

    # Manejo flexible: QR o link
    qr_code = None
    payment_url = None

    if "point_of_interaction" in preference:
        qr_code = preference["point_of_interaction"]["transaction_data"]["qr_code_base64"]
    else:
        payment_url = preference.get("init_point")

    return render_template(
        "payment_link.html",
        qr_code=qr_code,
        payment_url=payment_url,
        amount=amount
    )
@leaderboard_bp.route("/mp_notifications", methods=["POST"])
def mp_notifications():
    data = request.json
    payment_id = data.get("data", {}).get("id")

    if payment_id:
        payment = sdk.payment().get(payment_id)["response"]

        amount = payment["transaction_amount"]
        project_id = int(payment["external_reference"])
        status = payment["status"]  # aprobado, pendiente, rechazado

        if status == "approved":
            bid = Bid(
                amount=amount,
                project_id=project_id,
                mp_payment_id=payment_id,
                mp_status=status
            )
            db.session.add(bid)
            db.session.commit()

    return "OK", 200
@leaderboard_bp.route("/payment/<int:project_id>", methods=["POST"])
def payment(project_id):
    amount = request.form["amount"]

    preference_data = {
        "items": [
            {
                "title": f"Puja Proyecto {project_id}",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": float(amount),
            }
        ],
        "external_reference": str(project_id),
        "notification_url": "https://tu-dominio.com/mp_notifications",
        "purpose": "wallet_purchase"  # fuerza modo presencial
    }

    preference_response = sdk.preference().create(preference_data)
    preference = preference_response["response"]

    payment_url = preference.get("init_point")

    qr_base64 = None
    if "point_of_interaction" in preference:
        qr_base64 = preference["point_of_interaction"]["transaction_data"]["qr_code_base64"]

    return render_template(
        "payment_page.html",
        amount=amount,
        payment_url=payment_url,
        qr_base64=qr_base64
    )
