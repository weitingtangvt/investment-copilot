from __future__ import annotations

from flask import Blueprint, render_template


def create_chart_lab_module(auth_guard=None) -> Blueprint:
    bp = Blueprint("chart_lab_module", __name__)

    @bp.route("/chart-lab")
    def chart_lab_page():
        if auth_guard:
            guarded = auth_guard(lambda: render_template("chart_lab.html"))
            return guarded()
        return render_template("chart_lab.html")

    return bp
