 # -*- coding: utf-8 -*-
"""
TS Pattern Finder - Flask application entry point.

Industrial time series: Input -> Preprocess -> Query Generation -> Pattern Search -> Labeling
All route logic is in backend/routes/ blueprints.
"""
from __future__ import annotations

import os
import secrets
import time as _time

from flask import Flask, jsonify, make_response, redirect, request

from backend.app_config import cfg, cfg_search, cfg_time_units
from backend.i18n import i18n_context
from backend.json_provider import NaNSafeJSONProvider
from backend.log_config import setup_logging


def create_app() -> Flask:
    """Application factory."""
    setup_logging()

    app = Flask(__name__, template_folder="web/templates",
                static_folder="web/static", static_url_path="/static")
    # 비밀값은 yaml이 아닌 환경변수로만. 미설정이면 실행마다 임의 생성
    # (재시작 시 세션 쿠키 무효 — 유지하려면 TS_PATTERN_SECRET_KEY 설정).
    app.secret_key = os.environ.get("TS_PATTERN_SECRET_KEY") or secrets.token_hex(32)

    # 로그인 세션 쿠키. 계정 파일(config/local/users.yaml)이 있을 때만 로그인을 요구한다.
    from datetime import timedelta
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(hours=float(cfg("auth.session_hours"))),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    )

    # 업로드 한 건의 최대 크기. 넘으면 413 으로 끊어 메모리를 지킨다.
    app.config["MAX_CONTENT_LENGTH"] = int(cfg("csv.max_upload_mb")) * 1024 * 1024

    # NaN/Infinity -> null in JSON responses
    app.json_provider_class = NaNSafeJSONProvider
    app.json = NaNSafeJSONProvider(app)

    # /api/* 의 예기치 못한 예외를 JSON 으로 돌려준다. 기본 동작은 HTML 500
    # 페이지라, 화면의 r.json() 이 "Unexpected token '<'" 로 다시 깨져
    # 진짜 원인이 가려졌다.
    @app.errorhandler(Exception)
    def _json_errors(e):
        from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
        code = e.code if isinstance(e, HTTPException) else 500
        if not request.path.startswith("/api/"):
            return e if isinstance(e, HTTPException) else ("Internal Server Error", 500)
        if not isinstance(e, HTTPException):
            app.logger.exception("Unhandled error on %s", request.path)
        # 크기 초과는 기본 문구가 영어라 한도를 알 수 없다. 얼마까지 되는지 말해 준다.
        if isinstance(e, RequestEntityTooLarge):
            limit = cfg("csv.max_upload_mb")
            return jsonify(
                error=f"파일이 너무 큽니다. 한 번에 {limit}MB 까지 올릴 수 있습니다 "
                      f"(config/app.yaml 의 csv.max_upload_mb).",
                path=request.path), code
        return jsonify(error=str(getattr(e, "description", None) or e),
                       path=request.path), code

    from backend.db import close_request_connections
    app.teardown_appcontext(close_request_connections)

    from backend import auth as _auth

    @app.before_request
    def _require_login():
        return _auth.guard()

    @app.context_processor
    def _inject_user():
        return {"current_user": _auth.current_user(), "auth_enabled": _auth.auth_enabled()}

    @app.route("/login")
    def login_page():
        from flask import render_template
        if not _auth.auth_enabled() or _auth.current_user():
            return redirect("/")
        return render_template("login.html")

    @app.route("/api/login", methods=["POST"])
    def api_login():
        body = request.get_json(silent=True) or {}
        user = _auth.login(str(body.get("username", "")).strip(), str(body.get("password", "")))
        if user is None:
            return jsonify(error="아이디 또는 비밀번호가 맞지 않습니다"), 401
        return jsonify(ok=True, user=user)

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        _auth.logout()
        return jsonify(ok=True)

    @app.route("/api/auth/me")
    def api_auth_me():
        return jsonify(enabled=_auth.auth_enabled(), user=_auth.current_user())

    @app.before_request
    def _validate_json_object():
        if (request.path.startswith("/api/") and request.is_json
                and request.method in {"POST", "PUT", "PATCH", "DELETE"}):
            if not isinstance(request.get_json(), dict):
                return jsonify(error="JSON object required"), 400

    # Cache-bust for static assets + i18n translations.  Keep the token stable
    # for the lifetime of this process so browser caches remain useful between
    # page loads; restarting the service still publishes a fresh token.
    asset_version = os.environ.get("TS_PATTERN_ASSET_VERSION") or str(int(_time.time()))
    app.context_processor(i18n_context)

    @app.context_processor
    def _inject_cache_bust():
        return {"cache_bust": asset_version}

    # 자매 도구 링크. 절대 주소가 없으면 접속한 호스트에 포트만 바꿔 붙인다
    def _sibling_name(v):
        # 언어별 사전({ko, en})이면 현재 화면 언어로, 문자열이면 그대로
        if isinstance(v, dict):
            from backend.i18n import get_lang
            return v.get(get_lang()) or v.get("ko") or next(iter(v.values()), "")
        return v

    @app.context_processor
    def _inject_sibling():
        return {
            "sibling": {
                "name": _sibling_name(cfg("app.sibling.name")),
                "url": cfg("app.sibling.url") or "",
                "port": cfg("app.sibling.port"),
            }
        }

    # search.yaml -> window.SEARCH_CONFIG (single source for UI search defaults)
    @app.context_processor
    def _inject_search_config():
        return {
            "search_config": {
                "default_method": cfg_search("default_method"),
                "default_top_k": cfg_search("default_top_k"),
                "segment_top_k": cfg_search("segment_top_k"),
                "top_k_slider": cfg_search("top_k_slider"),
                "normalize": cfg_search("normalize.enabled"),
            }
        }

    # app.yaml -> window.APP_CONFIG (화면이 쓰는 길이·간격 기본값의 단일 소스).
    # 예전에는 JS 와 템플릿에 24 · 48 · 144 같은 숫자가 흩어져 있어, yaml 을
    # 고쳐도 화면은 옛 값을 그대로 썼다.
    @app.context_processor
    def _inject_app_config():
        return {
            "app_config": {
                "segment": {
                    "default_length": cfg("segment.default_length"),
                    "min_length": cfg("segment.min_length"),
                    "gap_size_default": cfg("segment.gap_size_default"),
                    "gap_size_min": cfg("segment.gap_size_min"),
                    "gap_size_max": cfg("segment.gap_size_max"),
                },
                "preset_shapes": {
                    "generation_resolution": cfg("preset_shapes.generation_resolution"),
                    "preview_length": cfg("preset_shapes.preview_length"),
                },
                "detection": {
                    "default_sampling_minutes": cfg("detection.default_sampling_minutes"),
                },
                "display": {"max_points": cfg("display.max_points")},
                "time_units": cfg_time_units(),
            }
        }

    # Language switch endpoint: GET /set_lang/en or /set_lang/ko
    @app.route("/set_lang/<lang>")
    def set_lang(lang):
        # `next`는 브라우저 검사·직접 링크용. 외부 URL로 열린 redirect가 되지 않게
        # 반드시 같은 서비스의 절대 경로만 허용한다. 일반 UI 클릭은 referrer로 복귀.
        requested_next = request.args.get("next", "")
        safe_next = requested_next if requested_next.startswith("/") and not requested_next.startswith("//") else ""
        referrer = safe_next or request.referrer or "/"
        resp = make_response("", 302)
        resp.headers["Location"] = referrer
        resp.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    # Register blueprints
    from backend.routes.pages import bp as pages_bp
    from backend.routes.datasets import bp as datasets_bp
    from backend.routes.data_load import bp as data_load_bp
    from backend.routes.data_transform import bp as data_transform_bp
    from backend.routes.periodicity_routes import bp as periodicity_bp
    from backend.routes.export_routes import bp as export_bp
    from backend.routes.search import bp as search_bp
    from backend.routes.upload import bp as upload_bp
    from backend.routes.preprocess_routes import bp as preprocess_bp
    from backend.routes.llm_routes import bp as llm_bp
    from backend.routes.patterns import bp as patterns_bp
    from backend.routes.segment_search_routes import bp as seg_search_bp
    from backend.routes.benchmark_import import bp as benchmark_bp
    from backend.routes.label_import import bp as label_import_bp
    from backend.routes.basket_routes import bp as basket_bp
    from backend.routes.preset_routes import bp as preset_bp
    from backend.routes.external_llm_routes import bp as external_llm_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(datasets_bp)
    app.register_blueprint(data_load_bp)
    app.register_blueprint(data_transform_bp)
    app.register_blueprint(periodicity_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(preprocess_bp)
    app.register_blueprint(llm_bp)
    app.register_blueprint(patterns_bp)
    app.register_blueprint(seg_search_bp)
    app.register_blueprint(benchmark_bp)
    app.register_blueprint(label_import_bp)
    app.register_blueprint(basket_bp)
    app.register_blueprint(preset_bp)
    app.register_blueprint(external_llm_bp)

    return app


app = create_app()

if __name__ == "__main__":
    from backend.startup import main
    main(app)
