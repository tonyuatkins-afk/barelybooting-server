"""Human-facing HTML pages. Server-rendered Jinja templates, no JS."""

from __future__ import annotations

from flask import Blueprint, Response, abort, render_template, request

from ..db import get_db


bp = Blueprint("browse", __name__, url_prefix="/cerberus")


PAGE_SIZE = 25

# Cap the page index so a hostile ``?page=999999`` request doesn't
# enumerate a pointless OFFSET. 4000 pages * 25 rows = 100k submissions,
# well past anything this project will plausibly see.
MAX_PAGE = 4000


def _paginate(
    select_sql: str,
    where_sql: str,
    args: tuple,
    page: int,
):
    """Run two queries: one COUNT(*) against the predicate alone, one
    paginated SELECT. ``select_sql`` is the full ``SELECT * FROM ... [WHERE]
    ORDER BY ...`` form; ``where_sql`` is just ``FROM submissions [WHERE
    ...]`` with no ORDER BY, used to compute the count cheaply."""
    db = get_db()
    total_count = db.execute(
        f"SELECT COUNT(*) AS n {where_sql}",
        args,
    ).fetchone()["n"]
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)

    # Clamp the requested page to [1, total_pages] to avoid empty reads
    # on pathological ``?page=`` values. Silent clamp is intentional:
    # user-typed URLs past the end should degrade to the last page, not
    # 404.
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE
    rows = db.execute(
        f"{select_sql} LIMIT ? OFFSET ?",
        args + (PAGE_SIZE, offset),
    ).fetchall()
    return rows, page, total_pages, total_count


def _page_arg() -> int:
    try:
        p = int(request.args.get("page", "1"))
    except ValueError:
        p = 1
    return max(1, min(p, MAX_PAGE))


@bp.route("/")
def browse_index():
    rows, page, total_pages, count = _paginate(
        "SELECT * FROM submissions ORDER BY received_at DESC",
        "FROM submissions",
        (),
        _page_arg(),
    )
    return render_template(
        "browse_list.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        total_count=count,
        title="All submissions",
        filter_kind=None,
        filter_value=None,
    )


@bp.route("/cpu/<cpu_class>")
def browse_cpu(cpu_class: str):
    rows, page, total_pages, count = _paginate(
        "SELECT * FROM submissions WHERE cpu_class = ? "
        "ORDER BY received_at DESC",
        "FROM submissions WHERE cpu_class = ?",
        (cpu_class.lower(),),
        _page_arg(),
    )
    return render_template(
        "browse_list.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        total_count=count,
        title=f"CPU class: {cpu_class}",
        filter_kind="cpu",
        filter_value=cpu_class,
    )


@bp.route("/machine/<hw_sig>")
def browse_machine(hw_sig: str):
    rows, page, total_pages, count = _paginate(
        "SELECT * FROM submissions WHERE hardware_signature = ? "
        "ORDER BY received_at DESC",
        "FROM submissions WHERE hardware_signature = ?",
        (hw_sig.lower(),),
        _page_arg(),
    )
    return render_template(
        "browse_list.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        total_count=count,
        title=f"Machine {hw_sig}",
        filter_kind="machine",
        filter_value=hw_sig,
    )


@bp.route("/unknown")
def browse_unknown():
    unknown_where = (
        "FROM submissions "
        "WHERE cpu_class IS NULL "
        "   OR cpu_class = 'unknown' "
        "   OR cpu_detected LIKE '%unknown%'"
    )
    rows, page, total_pages, count = _paginate(
        f"SELECT * {unknown_where} ORDER BY received_at DESC",
        unknown_where,
        (),
        _page_arg(),
    )
    return render_template(
        "browse_list.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        total_count=count,
        title="Unidentified hardware",
        filter_kind="unknown",
        filter_value=None,
    )


@bp.route("/run/<sub_id>")
def run_detail(sub_id: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM submissions WHERE id = ?", (sub_id,)
    ).fetchone()
    if not row:
        abort(404)
    return render_template("run_detail.html", row=row)


@bp.route("/export/all.csv")
def export_all_csv():
    """Stub. v0.7.1: implement with filter-aware streaming. For now,
    respond with a clear 501 so anyone who tries gets a useful signal
    rather than a silent empty CSV."""
    return Response(
        "# CSV export not implemented yet (planned for v0.7.1).\n"
        "# The browse pages at /cerberus/ have the same data in HTML.\n",
        status=501,
        mimetype="text/plain",
    )
