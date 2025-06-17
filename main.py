from fastapi import FastAPI, UploadFile, File, Request
import os
import zipfile
from weasyprint import HTML
import pandas as pd
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import re


app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

HTML_DIR = "output/html"
PDF_DIR = "output/pdf"
ZIP_FILE = "output/payslips.zip"



def parse_currency(value):
    if pd.isna(value):
        return 0.0
    value_str = str(value)
    value_clean = re.sub(r"[^\d\.-]", "", value_str)
    try:
        return float(value_clean)
    except ValueError:
        return 0.0


def number_to_words(n):
    import num2words
    return num2words.num2words(n, to='currency', lang='en_IN').replace('euro', 'Rupees').title()


@app.post("/upload/")
async def upload_excel(request: Request, file: UploadFile = File(...)):

    for folder in [HTML_DIR, PDF_DIR]:
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            os.remove(os.path.join(folder, f))

    full_df = pd.read_excel(file.file, header=None)
    file.file.seek(0)

    header_row_index = None   # find on n\whiuch row the  Employee Name which is header
    for i, row in full_df.iterrows():
        normalized_cells = [str(cell).strip().lower() for cell in row]
        if "employee name" in normalized_cells:
            header_row_index = i
            break

    if header_row_index is None:
        return {"error": "Could not find 'Employee Name' header row in file."}

    header = full_df.iloc[header_row_index]
    file.file.seek(0)
    employee_df = pd.read_excel(file.file, header=header_row_index)

    company_info_raw = full_df.iloc[:header_row_index]
    company_info = {}
    for _, row in company_info_raw.iterrows():  # which rows is before header it convert it to dict
        if pd.notna(row[0]) and pd.notna(row[1]):
            company_info[str(row[0]).strip()] = str(row[1]).strip()

    for _, row in employee_df.iterrows():
        name = str(row.get("Employee Name", "employee")).strip().replace(" ", "_") or "employee"

        # earnings = {
        #     col.replace("Earning", "").strip(): parse_currency(row[col])
        #     for col in employee_df.columns
        #     if isinstance(col, str) and col.strip().lower().startswith("earning") and "net" not in col.lower()
        #     }
        earnings = {
            re.sub(r'(?i)^earning', '', col).strip(): parse_currency(row[col]) #removes the earning at beginign
            for col in employee_df.columns
            if isinstance(col, str)
            and re.match(r'(?i)^earning', col.strip())  
            and not re.search(r'(?i)net', col) 
        }

        total_earnings = next(
            (val for key, val in earnings.items() if "total" in key.strip().lower()), 0)
       
        # deductions = {
        #     col.replace("Deduction", "").strip(): parse_currency(row[col])
        #     for col in employee_df.columns
        #     if isinstance(col, str) and col.lower().startswith("deduction") and "total" not in col.lower()
        # }
        deductions = {
            re.sub(r'(?i)^deduction', '', col).strip(): parse_currency(row[col])
            for col in employee_df.columns
            if isinstance(col, str)
            and re.match(r'(?i)^deduction', col.strip())  # starts with 'deduction', case-insensitive
            and not re.search(r'(?i)total', col)          # does not contain 'total', case-insensitive
        }

        total_deductions = sum(deductions.values())
        net_salary = total_earnings - total_deductions

        def get_column_value(row, possible_names):
            for col in row.index:
                clean_col = str(col).strip().lower()
                for name in possible_names:
                    if name.lower() in clean_col:
                        return row[col]
            return "-"


        personal_details = {
            "Name": get_column_value(row, ["Employee Name", "Name"]),
            "Employee ID": get_column_value(row, ["Employee Code", "Code"]),
            "Email": get_column_value(row, ["Email", "Employee Email"]),
            "PAN": get_column_value(row, ["Pan No", "PAN"]),
            "Aadhar": get_column_value(row, ["Aadhar No", "Aadhar"]),
            "Account": get_column_value(row, ["Account No", "Account"]),
            "Department": get_column_value(row, ["Department"]),
            "Designation": get_column_value(row, ["Designation"]),
            "Joining Date": get_column_value(row, ["Joining Date", "Date of Joining", "DOJ"]),
        }


        order_details = list(personal_details.keys())

        context = {
            "request": request,
            "personal_details": personal_details,
            "order_details": order_details,
            "salary_details": {
                "Earnings": earnings,
                "Deductions": deductions
            },
            "totals": {
                "Earnings": f"{total_earnings:,.2f}",
                "Deductions": f"{total_deductions:,.2f}"
            },
            "net_salary": f"{net_salary:,.2f}",
            "net_salary_in_words": number_to_words(net_salary),
            "monthyear": company_info.get("Month Year", "-"),
            "salary_date": company_info.get("Salary Date", "-"),
            "hr": company_info.get("HR", "-"),
            "address": company_info.get("Address", "-")
        }

        html_content = templates.get_template("slip.html").render(context)
        html_path = os.path.join(HTML_DIR, f"{name}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        pdf_path = os.path.join(PDF_DIR, f"{name}.pdf")
        HTML(html_path).write_pdf(pdf_path)

    with zipfile.ZipFile(ZIP_FILE, "w") as zipf:
        for folder in [HTML_DIR, PDF_DIR]:
            for file in os.listdir(folder):
                path = os.path.join(folder, file)
                zipf.write(path, arcname=os.path.join(os.path.basename(folder), file))

    return FileResponse(ZIP_FILE, filename="payslips.zip", media_type='application/zip')
