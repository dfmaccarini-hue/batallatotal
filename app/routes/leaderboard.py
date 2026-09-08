import os
from flask import Blueprint, render_template, request
from app.models import Project, Bid
from app import db, mp_sdk   
from sqlalchemy import func

leaderboard_bp = Blueprint("leaderboard", __name__)

# Switch automático para notification_url
if os.getenv("FLASK_ENV") == "development":
    NOTIFICATION_URL = "https://abcd1234.ngrok.io/mp_notifications"
else:
    NOTIFICATION_URL = "https://batallatotal.onrender.com/mp_notifications"


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
                "success": "https://batallatotal.onrender.com/success",
                "failure": "https://batallatotal.onrender.com/failure",
                "pending": "https://batallatotal.onrender.com/pending"
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
        puesto_anterior = getattr(project, "last_rank", puesto_actual)
        if puesto_actual < puesto_anterior:
            movimientos[project.id] = "up"
        elif puesto_actual > puesto_anterior:
            movimientos[project.id] = "down"
        else:
            movimientos[project.id] = "same"
        # actualizar el atributo para la próxima vez
        project.last_rank = puesto_actual

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
            "success": "https://batallatotal.onrender.com/success",
            "failure": "https://batallatotal.onrender.com/failure",
            "pending": "https://batallatotal.onrender.com/pending"
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
            "success": "https://batallatotal.onrender.com/success",
            "failure": "https://batallatotal.onrender.com/failure",
            "pending": "https://batallatotal.onrender.com/pending"
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


@leaderboard_bp.route("/mp_notifications", methods=["POST"])
def mp_notifications():
    data = request.json
    payment_id = data.get("data", {}).get("id")

    if payment_id:
        payment = mp_sdk.payment().get(payment_id)["response"]

        amount = payment["transaction_amount"]
        status = payment["status"]
        external_ref = payment["external_reference"]

        if status == "approved":
            existing_bid = Bid.query.filter_by(mp_payment_id=payment_id).first()
            if existing_bid:
                return "Bid already processed", 200

            if "|" in external_ref:
                try:
                    name, description, category = external_ref.split("|")
                except ValueError:
                    return "Invalid external_reference format", 400

                project = Project(name=name, description=description, category=category)
                db.session.add(project)
                db.session.commit()

                bid = Bid(
                    amount=amount,
                    project_id=project.id,
                    mp_payment_id=payment_id,
                    mp_status=status
                )
                db.session.add(bid)
                db.session.commit()

            else:
                try:
                    project_id = int(external_ref)
                except ValueError:
                    return "Invalid project_id in external_reference", 400

                project = Project.query.get(project_id)
                if not project:
                    return "Project not found", 404

                bid = Bid(
                    amount=amount,
                    project_id=project.id,
                    mp_payment_id=payment_id,
                    mp_status=status
                )
                db.session.add(bid)
                db.session.commit()

    return "OK", 200


@leaderboard_bp.route("/success")
def success():
    external_ref = request.args.get("external_reference")
    payment_id = request.args.get("payment_id")

    project = None
    bid = None

    if external_ref:
        if "|" in external_ref:
            # Caso proyecto nuevo
            try:
                name, description, category = external_ref.split("|")
                project = {"name": name, "description": description, "category": category}
            except ValueError:
                project = None
        else:
            # Caso puja sobre proyecto existente
            try:
                project_id = int(external_ref)
                project_obj = Project.query.get(project_id)
                if project_obj:
                    project = {
                        "name": project_obj.name,
                        "description": project_obj.description,
                        "category": project_obj.category
                    }
            except ValueError:
                project = None

    if payment_id:
        bid = Bid.query.filter_by(mp_payment_id=payment_id).first()

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
