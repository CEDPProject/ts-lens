# -*- coding: utf-8 -*-
"""Page rendering routes."""
from backend.app_config import cfg_quick_start
from flask import Blueprint, render_template, request

bp = Blueprint("pages", __name__)


@bp.route("/")
def landing():
    return render_template("landing.html", active_page="home")


@bp.route("/finder")
def index():
    # 시작 카드는 config/quick_start.yaml 이 단일 소스. 여기서는 그대로 넘긴다.
    qs = cfg_quick_start()
    cards = qs.get("cards", []) if qs.get("enabled", True) else []
    return render_template(
        "index.html",
        active_page="finder",
        quick_cards=cards[: qs.get("max_cards", 4)],
    )


@bp.route("/data")
def data_viewer():
    return render_template("data.html", active_page="data")


@bp.route("/preprocess")
def preprocess_page():
    return render_template("preprocess.html", active_page="preprocess")


@bp.route("/about")
def about():
    print_all = request.args.get("print") == "all"
    return render_template("about.html", active_page="about", print_all=print_all)


@bp.route("/labeler")
def labeler():
    return render_template("labeler.html", active_page="labeler")


@bp.route("/discovery")
def discovery():
    return render_template("discovery.html", active_page="discovery")




@bp.route("/data/models/external")
def external_models():
    return render_template("models.html", active_page="data", model_page="external")


@bp.route("/data/models/local")
def local_models():
    return render_template("models.html", active_page="data", model_page="local")
