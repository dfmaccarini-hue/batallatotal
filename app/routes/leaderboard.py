
from flask import Blueprint, render_template, request
from app.models import Project, Bid
from app import db, mp_sdk   
from sqlalchemy import func
from dotenv import load_dotenv
import os

load_dotenv()
# Inicializar variables
BASE_URL = os.getenv("BASE_URL")
NOTIFICATION_URL = os.getenv("NOTIFICATION_URL")

# Mostrar en terminal
print("🔧 BASE_URL:", BASE_URL)
print("🔧 NOTIFICATION_URL:", NOTIFICATION_URL)

leaderboard_bp = Blueprint("leaderboard", __name__)


def _current_ranks():
    rows = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .group_by(Project.id)
        .all()
    )
    ordered = sorted(rows, key=lambda x: x[1] or 0, reverse=True)
    return {proj_id: idx + 1 for idx, (proj_id, _total) in enumerate(ordered)}


def _extract_payment_ids():
    payload = request.get_json(silent=True) or {}
    payment_ids = []

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("id"):
        payment_ids.append(str(data["id"]))

    for key in ("id", "resource"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if value:
            value = str(value).rstrip("/").split("/")[-1]
            if value.isdigit():
                payment_ids.append(value)

    for key in ("data.id", "id"):
        value = request.args.get(key)
        if value:
            payment_ids.append(str(value))

    topic = (
        (payload.get("type") if isinstance(payload, dict) else None)
        or (payload.get("topic") if isinstance(payload, dict) else None)
        or request.args.get("topic")
        or request.args.get("type")
        or ""
    )
    topic = str(topic).lower()

    unique_ids = list(dict.fromkeys(payment_ids))
    return unique_ids, topic


def _payments_from_merchant_order(order_id):
    try:
        order = mp_sdk.merchant_order().get(order_id)["response"]
    except Exception as exc:
        print("⚠️ No se pudo leer merchant_order:", order_id, exc)
        return []

    payment_ids = []
    for payment in order.get("payments") or []:
        payment_id = payment.get("id")
        if payment_id:
            payment_ids.append(str(payment_id))
    return payment_ids


def process_mp_payment(payment_id):
    """Guarda proyecto/puja cuando Mercado Pago confirma un pago aprobado."""
    if not payment_id:
        return None, None

    payment_id = str(payment_id)
    existing_bid = Bid.query.filter_by(mp_payment_id=payment_id).first()
    if existing_bid:
        return existing_bid.project, existing_bid

    try:
        payment = mp_sdk.payment().get(payment_id)["response"]
    except Exception as exc:
        print("⚠️ Error consultando pago en MP:", payment_id, exc)
        return None, None

    if not payment or payment.get("id") is None:
        print("⚠️ Pago no encontrado en MP:", payment_id, payment)
        return None, None

    status = payment.get("status")
    amount = payment.get("transaction_amount")
    external_ref = payment.get("external_reference") or ""
    print(f"💳 MP payment {payment_id} status={status} ref={external_ref} amount={amount}")

    if status != "approved":
        return None, None

    ranks_before = _current_ranks()

    if "|" in external_ref:
        try:
            name, description, category = external_ref.split("|", 2)
        except ValueError:
            print("⚠️ external_reference inválida:", external_ref)
            return None, None

        project = Project(name=name, description=description, category=category)
        db.session.add(project)
        db.session.flush()
    else:
        try:
            project_id = int(external_ref)
        except (TypeError, ValueError):
            print("⚠️ project_id inválido en external_reference:", external_ref)
            return None, None

        project = Project.query.get(project_id)
        if not project:
            print("⚠️ Proyecto no encontrado:", project_id)
            return None, None

    bid = Bid(
        amount=amount,
        project_id=project.id,
        mp_payment_id=payment_id,
        mp_status=status,
    )
    db.session.add(bid)
    db.session.flush()

    ranks_after = _current_ranks()
    for ranked_project in Project.query.all():
        previous = ranks_before.get(ranked_project.id)
        if previous is None:
            ranked_project.last_rank = (ranks_after.get(ranked_project.id) or 1) + 1
        else:
            ranked_project.last_rank = previous

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print("⚠️ Error guardando puja:", exc)
        existing_bid = Bid.query.filter_by(mp_payment_id=payment_id).first()
        if existing_bid:
            return existing_bid.project, existing_bid
        return None, None

    print(f"✅ Puja guardada: project={project.id} amount={amount} payment={payment_id}")
    return project, bid

# Switch automático para notification_url
#if os.getenv("FLASK_ENV") == "development":
#NOTIFICATION_URL = "https://abcd1234.ngrok.io/mp_notifications"
#BASE_URL="https://192.168.33.48:5001"
#print(f"NOTIFICATION_URL = {NOTIFICATION_URL}")
#else:
#    NOTIFICATION_URL = "https://batallatotal.onrender.com/mp_notifications"
#    BASE_URL="https://batallatotal.onrender.com"

@leaderboard_bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name = request.form["name"]
        description = request.form["description"]
        category = request.form["category"]
        initial_bid = float(request.form.get("initial_bid"))

        external_ref = f"{name}|{description}|{category}"

        preference_data = {
            "items": [
                {
                    "title": f"Proyecto {name}",
                    "quantity": 1,
                    "currency_id": "ARS",
                    "unit_price": initial_bid,
                }
            ],
            "external_reference": external_ref,
            "notification_url": NOTIFICATION_URL,
            "back_urls": {
                "success": f"{BASE_URL}/success",
                "failure": f"{BASE_URL}/failure",
                "pending": f"{BASE_URL}/pending"
            },
            "auto_return": "approved"
        }

        preference_response = mp_sdk.preference().create(preference_data)
        preference = preference_response["response"]
        payment_url = preference.get("init_point")

        # 🔹 Calcular puestos proyectados para proyecto nuevo
        general_query = (
            db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
            .outerjoin(Bid)
            .group_by(Project.id)
            .all()
        )

        ranking_general = sorted(
            general_query + [(-1, initial_bid)],  # -1 como ID temporal
            key=lambda x: x[1] or 0,
            reverse=True
        )
        puesto_general = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(ranking_general)}.get(-1)

        category_query = (
            db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
            .outerjoin(Bid)
            .filter(Project.category == category)
            .group_by(Project.id)
            .all()
        )

        ranking_categoria = sorted(
            category_query + [(-1, initial_bid)],
            key=lambda x: x[1] or 0,
            reverse=True
        )
        puesto_categoria = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(ranking_categoria)}.get(-1)

        return render_template(
            "payment_page.html",
            amount=initial_bid,
            payment_url=payment_url,
            puesto_general=puesto_general,
            puesto_categoria=puesto_categoria
        )

    selected_category = request.args.get("category")

    query = (
        db.session.query(
            Project,
            func.sum(Bid.amount).label("total_bids"),
            func.max(Bid.amount).label("max_bid")
        )
        .outerjoin(Bid)
        .group_by(Project.id)
        .order_by(func.sum(Bid.amount).desc())
    )

    if selected_category:
        query = query.filter(Project.category == selected_category)

    projects = query.all()

    categories = db.session.query(Project.category).distinct().all()
    categories = [c[0] for c in categories]

    # 🔹 Calcular movimientos (subió, bajó, igual)
    movimientos = {}
    for idx, (project, total_bids, max_bid) in enumerate(projects, start=1):
        puesto_actual = idx
        puesto_anterior = project.last_rank if project.last_rank is not None else puesto_actual
        if puesto_actual < puesto_anterior:
            movimientos[project.id] = "up"
        elif puesto_actual > puesto_anterior:
            movimientos[project.id] = "down"
        else:
            movimientos[project.id] = "same"

    return render_template(
        "leaderboard.html",
        projects=projects,
        categories=categories,
        selected_category=selected_category,
        movimientos=movimientos
    )

@leaderboard_bp.route("/add_bid/<int:project_id>", methods=["POST"])
def add_bid(project_id):
    amount = float(request.form["amount"])

    preference_data = {
        "items": [
            {
                "title": f"Puja Proyecto {project_id}",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": amount,
            }
        ],
        "external_reference": str(project_id),
        "notification_url": NOTIFICATION_URL,
        "back_urls": {
            "success": f"{BASE_URL}/success",
            "failure": f"{BASE_URL}/failure",
            "pending": f"{BASE_URL}/pending"
        },
        "auto_return": "approved"
    }

    preference_response = mp_sdk.preference().create(preference_data)
    preference = preference_response["response"]

    qr_code = None
    payment_url = preference.get("init_point")

    if "point_of_interaction" in preference:
        qr_code = preference["point_of_interaction"]["transaction_data"]["qr_code_base64"]

    # 🔹 Calcular puestos proyectados
    project = Project.query.get(project_id)

    total_actual = (
        db.session.query(func.sum(Bid.amount))
        .filter(Bid.project_id == project_id)
        .scalar()
    ) or 0
    total_proyectado = total_actual + amount

    # Ranking general
    general_query = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .group_by(Project.id)
        .all()
    )
    ranking_general = sorted(
        [(proj_id, total if proj_id != project_id else total_proyectado)
         for proj_id, total in general_query],
        key=lambda x: x[1],
        reverse=True
    )
    puesto_general = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(ranking_general)}.get(project_id)

    # Ranking por categoría
    category_query = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .filter(Project.category == project.category)
        .group_by(Project.id)
        .all()
    )
    ranking_categoria = sorted(
        [(proj_id, total if proj_id != project_id else total_proyectado)
         for proj_id, total in category_query],
        key=lambda x: x[1],
        reverse=True
    )
    puesto_categoria = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(ranking_categoria)}.get(project_id)

    return render_template(
        "payment_link.html",
        qr_code=qr_code,
        payment_url=payment_url,
        amount=amount,
        puesto_general=puesto_general,
        puesto_categoria=puesto_categoria
    )


@leaderboard_bp.route("/payment/<int:project_id>", methods=["POST"])
def payment(project_id):
    amount = float(request.form["amount"])

    preference_data = {
        "items": [
            {
                "title": f"Puja Proyecto {project_id}",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": amount,
            }
        ],
        "external_reference": str(project_id),
        "notification_url": NOTIFICATION_URL,
        "back_urls": {
            "success": f"{BASE_URL}/success",
            "failure": f"{BASE_URL}/failure",
            "pending": f"{BASE_URL}/pending"
        },
        "auto_return": "approved"
    }

    preference_response = mp_sdk.preference().create(preference_data)
    preference = preference_response["response"]

    payment_url = preference.get("init_point")
    qr_base64 = None

    if "point_of_interaction" in preference:
        qr_base64 = preference["point_of_interaction"]["transaction_data"]["qr_code_base64"]

    # 🔹 Calcular puestos proyectados
    project = Project.query.get(project_id)

    total_actual = (
        db.session.query(func.sum(Bid.amount))
        .filter(Bid.project_id == project_id)
        .scalar()
    ) or 0
    total_proyectado = total_actual + amount

    # Ranking general
    general_query = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .group_by(Project.id)
        .all()
    )
    ranking_general = sorted(
        [(proj_id, total if proj_id != project_id else total_proyectado)
         for proj_id, total in general_query],
        key=lambda x: x[1],
        reverse=True
    )
    puesto_general = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(ranking_general)}.get(project_id)

    # Ranking por categoría
    category_query = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .filter(Project.category == project.category)
        .group_by(Project.id)
        .all()
    )
    ranking_categoria = sorted(
        [(proj_id, total if proj_id != project_id else total_proyectado)
         for proj_id, total in category_query],
        key=lambda x: x[1],
        reverse=True
    )
    puesto_categoria = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(ranking_categoria)}.get(project_id)

    return render_template(
        "payment_page.html",
        amount=amount,
        payment_url=payment_url,
        qr_base64=qr_base64,
        puesto_general=puesto_general,
        puesto_categoria=puesto_categoria
    )


@leaderboard_bp.route("/mp_notifications", methods=["GET", "POST"])
def mp_notifications():
    payment_ids, topic = _extract_payment_ids()
    print("🔔 Notificación MP:", topic, payment_ids, request.args.to_dict(), request.get_json(silent=True))

    if "merchant_order" in topic:
        order_id = payment_ids[0] if payment_ids else request.args.get("id")
        payment_ids = _payments_from_merchant_order(order_id)

    for payment_id in payment_ids:
        process_mp_payment(payment_id)

    return "OK", 200


@leaderboard_bp.route("/success")
def success():
    payment_id = request.args.get("payment_id") or request.args.get("collection_id")
    merchant_order_id = request.args.get("merchant_order_id")
    external_ref = request.args.get("external_reference")
    print("🏁 Success MP:", dict(request.args))

    project = None
    bid = None

    if payment_id:
        project, bid = process_mp_payment(payment_id)

    if not bid and merchant_order_id:
        for extra_payment_id in _payments_from_merchant_order(merchant_order_id):
            project, bid = process_mp_payment(extra_payment_id)
            if bid:
                break

    if not project and external_ref:
        if "|" in external_ref:
            try:
                name, description, category = external_ref.split("|", 2)
                project = {"name": name, "description": description, "category": category}
            except ValueError:
                project = None
        else:
            try:
                project_obj = Project.query.get(int(external_ref))
                if project_obj:
                    project = project_obj
            except ValueError:
                project = None

    return render_template("success.html", project=project, bid=bid)


@leaderboard_bp.route("/failure")
def failure():
    return render_template("failure.html")


@leaderboard_bp.route("/pending")
def pending():
    return render_template("pending.html")

@leaderboard_bp.route("/history/<int:project_id>")
def history(project_id):
    project = Project.query.get_or_404(project_id)
    bids = Bid.query.filter_by(project_id=project_id).all()

    # Ranking general
    general_query = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .group_by(Project.id)
        .order_by(func.sum(Bid.amount).desc())
        .all()
    )
    general_ranking = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(general_query)}
    puesto_general = general_ranking.get(project.id)

    # Ranking por categoría
    category_query = (
        db.session.query(Project.id, func.sum(Bid.amount).label("total_bids"))
        .outerjoin(Bid)
        .filter(Project.category == project.category)
        .group_by(Project.id)
        .order_by(func.sum(Bid.amount).desc())
        .all()
    )
    category_ranking = {proj_id: idx+1 for idx, (proj_id, total) in enumerate(category_query)}
    puesto_categoria = category_ranking.get(project.id)

    return render_template(
        "history.html",
        project=project,
        bids=bids,
        puesto_general=puesto_general,
        puesto_categoria=puesto_categoria
    )

@leaderboard_bp.route("/history_all")
def history_all():
    bids = Bid.query.order_by(Bid.id.desc()).all()
    return render_template("history_all.html", bids=bids)
