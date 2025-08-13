from flask import Flask, render_template, request, redirect, url_for, send_file
import psycopg2
import psycopg2.extras
from datetime import datetime
import os
import io
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from decimal import Decimal

app = Flask(__name__)

# PostgreSQL connection using DATABASE_URL (set in hosting platform)
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            payer VARCHAR(10) NOT NULL,
            item TEXT,
            amount NUMERIC(10, 2) NOT NULL
        )"""
    )
    conn.commit()
    cur.close()
    conn.close()

init_db()

# Friendly names shown in UI and exports
NAME_MAP = {
    "me": "Soumajit",
    "her": "Rimpa"
}

@app.route("/", methods=["GET"])
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM expenses ORDER BY date DESC, id DESC")
    expenses = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("index.html", expenses=expenses, NAME_MAP=NAME_MAP)

@app.route("/add", methods=["POST"])
def add_expense():
    date = request.form.get("date") or datetime.now().strftime("%Y-%m-%d")
    payer = request.form.get("payer")
    item = request.form.get("item", "").strip()
    amount = request.form.get("amount", "0").strip()

    try:
        amt = float(amount)
    except ValueError:
        return "Invalid amount", 400

    if payer not in ("me", "her"):
        return "Invalid payer", 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expenses (date, payer, item, amount) VALUES (%s, %s, %s, %s)",
        (date, payer, item, amt),
    )
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))

@app.route("/delete/<int:expense_id>", methods=["POST"])
def delete_expense(expense_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id = %s", (expense_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))

@app.route("/summary", methods=["GET"])
def summary():
    qmonth = request.args.get("month")
    qyear = request.args.get("year")
    now = datetime.now()
    month = int(qmonth) if qmonth and qmonth.isdigit() else now.month
    year = int(qyear) if qyear and qyear.isdigit() else now.year

    start = datetime(year, month, 1).strftime("%Y-%m-%d")
    end = datetime(year + (month // 12), (month % 12) + 1, 1).strftime("%Y-%m-%d")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM expenses WHERE date >= %s AND date < %s ORDER BY date ASC",
        (start, end),
    )
    rows = cur.fetchall()

    me_total = sum(r["amount"] for r in rows if r["payer"] == "me")
    her_total = sum(r["amount"] for r in rows if r["payer"] == "her")
    total = me_total + her_total
    share = total / Decimal("2.0") if total else Decimal("0.0")

    if me_total > share:
        status = "she_owes"
        amount = round(me_total - share, 2)
        message = f"{NAME_MAP['her']} owes {NAME_MAP['me']} ₹{amount:.2f}"
    elif her_total > share:
        status = "you_owe"
        amount = round(her_total - share, 2)
        message = f"{NAME_MAP['me']} owes {NAME_MAP['her']} ₹{amount:.2f}"
    else:
        status = "settled"
        amount = 0.0
        message = "Settled — no one owes anything."

    cur.close()
    conn.close()

    months = []
    for i in range(12):
        d = datetime(now.year, now.month, 1)
        yr = d.year
        m = d.month - i
        while m <= 0:
            m += 12
            yr -= 1
        months.append({"year": yr, "month": m, "label": f"{yr}-{m:02d}"})

    return render_template(
        "summary.html",
        rows=rows,
        me_total=round(me_total, 2),
        her_total=round(her_total, 2),
        total=round(total, 2),
        share=round(share, 2),
        message=message,
        status=status,
        amount=amount,
        selected_month=month,
        selected_year=year,
        months=months,
        NAME_MAP=NAME_MAP
    )

@app.route("/export/csv")
def export_csv():
    qmonth = request.args.get("month")
    qyear = request.args.get("year")
    now = datetime.now()
    month = int(qmonth) if qmonth and qmonth.isdigit() else now.month
    year = int(qyear) if qyear and qyear.isdigit() else now.year

    start = datetime(year, month, 1).strftime("%Y-%m-%d")
    end = datetime(year + (month // 12), (month % 12) + 1, 1).strftime("%Y-%m-%d")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM expenses WHERE date >= %s AND date < %s ORDER BY date ASC",
        (start, end),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Payer", "PayerName", "Item", "Amount"])
    for r in rows:
        writer.writerow([r["date"], r["payer"], NAME_MAP[r["payer"]], r["item"], f"{r['amount']:.2f}"])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8"))
    mem.seek(0)
    filename = f"shared_expenses_{year}-{month:02d}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)

@app.route("/export/pdf")
def export_pdf():
    qmonth = request.args.get("month")
    qyear = request.args.get("year")
    now = datetime.now()
    month = int(qmonth) if qmonth and qmonth.isdigit() else now.month
    year = int(qyear) if qyear and qyear.isdigit() else now.year

    start = datetime(year, month, 1).strftime("%Y-%m-%d")
    end = datetime(year + (month // 12), (month % 12) + 1, 1).strftime("%Y-%m-%d")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM expenses WHERE date >= %s AND date < %s ORDER BY date ASC",
        (start, end),
    )
    rows = cur.fetchall()

    me_total = sum(r["amount"] for r in rows if r["payer"] == "me")
    her_total = sum(r["amount"] for r in rows if r["payer"] == "her")
    total = me_total + her_total
    share = total / Decimal("2.0") if total else Decimal("0.0")
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    x = margin
    y = height - margin

    c.setFont("Helvetica-Bold", 14)
    c.drawString(x, y, f"Shared Expenses — {year}-{month:02d}")
    y -= 8 * mm

    c.setFont("Helvetica", 11)
    c.drawString(x, y, f"{NAME_MAP['me']} paid: ₹{me_total:.2f}")
    y -= 6 * mm
    c.drawString(x, y, f"{NAME_MAP['her']} paid: ₹{her_total:.2f}")
    y -= 6 * mm
    c.drawString(x, y, f"Total: ₹{total:.2f}    Equal share: ₹{share:.2f}")
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "Expenses:")
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    table_header = ["Date", "Payer", "Item", "Amount"]
    col_x = [x, x + 40*mm, x + 80*mm, x + 150*mm]
    for i, h in enumerate(table_header):
        c.drawString(col_x[i], y, h)
    y -= 5 * mm
    c.line(x, y, width - margin, y)
    y -= 6 * mm

    for r in rows:
        if y < margin + 30*mm:
            c.showPage()
            y = height - margin
        c.drawString(col_x[0], y, r["date"].strftime("%Y-%m-%d") if isinstance(r["date"], datetime) else str(r["date"]))
        c.drawString(col_x[1], y, NAME_MAP[r["payer"]])
        item = (r["item"] or "")
        max_item_len = 40
        if len(item) > max_item_len:
            item = item[:max_item_len-3] + "..."
        c.drawString(col_x[2], y, item)
        c.drawRightString(col_x[3] + 30*mm, y, f"₹{r['amount']:.2f}")
        y -= 6 * mm

    y -= 8 * mm
    if me_total > share:
        message = f"{NAME_MAP['her']} owes {NAME_MAP['me']} ₹{(me_total - share):.2f}"
    elif her_total > share:
        message = f"{NAME_MAP['me']} owes {NAME_MAP['her']} ₹{(her_total - share):.2f}"
    else:
        message = "Settled — no one owes anything."
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Result: " + message)

    c.save()
    buffer.seek(0)

    filename = f"shared_expenses_{year}-{month:02d}.pdf"
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
