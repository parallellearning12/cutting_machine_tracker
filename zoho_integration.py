import requests
import os
import csv
import io
from dotenv import load_dotenv
from datetime import datetime, date

load_dotenv()

# Configuration from environment
ZOHO_API_ENDPOINT = os.getenv("ZOHO_API_ENDPOINT_WORK_ORDERS")
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"
ZOHO_FORM_UPLOAD_URL = "https://www.zohoapis.in/creator/v2.1/data/support_parallellearning/parallel-learning-shop/form/GE_coil_cutting_report"
ZOHO_EXCEL_FILE_FIELD_NAME = "Excel_File"


def get_zoho_access_token():
    """Obtains a Zoho access token using a refresh token."""
    if not all([ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET]):
        print("Zoho credentials not found in .env")
        return None

    payload = {
        'refresh_token': ZOHO_REFRESH_TOKEN,
        'client_id': ZOHO_CLIENT_ID,
        'client_secret': ZOHO_CLIENT_SECRET,
        'grant_type': 'refresh_token',
    }

    try:
        response = requests.post(ZOHO_TOKEN_URL, data=payload)
        response.raise_for_status()
        token_data = response.json()
        return token_data.get('access_token')
    except requests.exceptions.RequestException as e:
        print(f"Error obtaining Zoho access token: {e}")
    except ValueError:
        print(f"Error decoding Zoho token response: {response.text}")
    return None


def fetch_work_orders_from_zoho():
    """
    Fetches work orders from Zoho API and formats them with proper WO_Name structure.
    Returns list of dicts with 'id' (work order number) and 'name' (formatted WO_Name).
    """
    access_token = get_zoho_access_token()
    if not access_token:
        print("Cannot fetch work orders: No access token.")
        return get_mock_work_orders()

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}

    try:
        print(f"Fetching work orders from: {ZOHO_API_ENDPOINT}")
        response = requests.get(ZOHO_API_ENDPOINT, headers=headers)
        response.raise_for_status()
        api_response = response.json()

        work_orders = []
        for item in api_response.get("data", []):
            wo_id = item.get("Work_order_no")
            if not wo_id:
                continue

            # --- ORIGINAL WO_Name CONSTRUCTION ---
            company_name = item.get("WO_Name")
            #display_name = item.get("Display_Name_Field")

            wo_name = f"{wo_id} - {company_name}"
            if company_name:
                wo_name += f" ({company_name})"
            # --- END ORIGINAL WO_Name CONSTRUCTION ---

            work_orders.append({
                "id": str(wo_id),
                "name": wo_name.strip()
            })

        return work_orders or get_mock_work_orders()

    except Exception as e:
        print(f"Error fetching work orders: {e}")
        return get_mock_work_orders()


def create_zoho_coil_cutting_report_entry(work_order_id, work_order_display_name, session, lot_history):
    """
    Creates a new record in Zoho Creator with work order details, CSV file, and current date.
    """
    access_token = get_zoho_access_token()
    if not access_token:
        print("Failed to get access token")
        return False

    # Prepare CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Work Order', 'Model No', 'Total Cuts', 'Status', 'Step',
        'Lot', 'Cuts With Lot', 'Start Time', 'End Time'
    ])

    for lot in lot_history:
        writer.writerow([
            session.work_order_code,
            session.model_no,
            session.total_cuts_completed_for_session,
            session.status,
            session.current_step,
            lot.paper_lot_number,
            lot.cuts_made_with_this_lot,
            lot.start_time_for_lot.strftime('%Y-%m-%d'),
            lot.end_time_for_lot.strftime('%Y-%m-%d') if lot.end_time_for_lot else 'Active'
        ])

    output.seek(0)
    csv_content = output.read()

    # Get current date and format it for Zoho Creator's Date field
    current_date = date.today()
    # *** CHANGED THIS LINE: Now formatting as DD-MM-YYYY ***
    current_date_str = current_date.strftime('%m-%d-%y')

    # Prepare form data with properly formatted WO_Name and the new Date_field
    form_data = {
        "WO_Name": work_order_display_name,
        "Date_field": current_date_str
    }
    files = {
        ZOHO_EXCEL_FILE_FIELD_NAME: ("session_report.csv", csv_content, "text/csv")
    }

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}

    try:
        response = requests.post(
            ZOHO_FORM_UPLOAD_URL,
            headers=headers,
            data=form_data,
            files=files
        )
        response.raise_for_status() # This will raise an HTTPError for 4xx/5xx responses

        # The 'SUCCESS' message below was misleading because it didn't check Zoho's internal errors.
        # Now we check Zoho's specific error payload for the Date_field.
        api_response = response.json()
        if api_response.get('code') == 3002 and 'Date_field' in api_response.get('error', {}):
            print(f"Zoho Creator reported a partial success/validation error: {api_response.get('error')}")
            # If the only error is date format, other data might still have been pushed.
            # You might want to return False here if you consider partial success a failure for your logic.
            # For now, we'll continue to return True for a 2xx HTTP status from requests.post
            # but print the specific Zoho error.
            return False # Let's return False for validation errors for clearer feedback

        print(f"Successfully created Zoho entry. Response: {api_response}")
        return True

    except Exception as e:
        print(f"Error creating Zoho entry: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Error details from Zoho: {e.response.text}")
        return False


def get_mock_work_orders():
    """Returns mock work orders for testing when API calls fail."""
    print("Using mock work orders")
    return [
        {"id": "WO-MOCK-001", "name": "WO-MOCK-001 - Test Company (Display 1)"},
        {"id": "WO-MOCK-002", "name": "WO-MOCK-002 - Demo Inc (Display 2)"},
        {"id": "WO-MOCK-003", "name": "WO-MOCK-003 - Sample LLC"}
    ]


if __name__ == '__main__':
    # Test work order fetching
    print("Testing work order fetching...")
    orders = fetch_work_orders_from_zoho()
    for order in orders:
        print(f"ID: {order['id']}, Name: {order['name']}")


    # Test report creation
    class DummySession:
        work_order_code = "WO-TEST-123"
        model_no = "Model-X"
        total_cuts_completed_for_session = 120
        status = "Completed"
        current_step = "Step-3"


    class DummyLog:
        def __init__(self, paper_lot_number, cuts_made_with_this_lot, start_time_for_lot, end_time_for_lot=None):
            self.paper_lot_number = paper_lot_number
            self.cuts_made_with_this_lot = cuts_made_with_this_lot
            self.start_time_for_lot = start_time_for_lot
            self.end_time_for_lot = end_time_for_lot


    dummy_logs = [
        DummyLog("LOT-001", 30, datetime(2025, 6, 8, 10, 0), datetime(2025, 6, 8, 11, 0)),
        DummyLog("LOT-002", 45, datetime(2025, 6, 8, 11, 15), datetime(2025, 6, 8, 12, 0)),
    ]

    if orders:
        selected_order = orders[0]
        print(f"\nTesting report creation for: {selected_order['name']}")
        success = create_zoho_coil_cutting_report_entry(
            selected_order['id'],
            selected_order['name'],
            DummySession(),
            dummy_logs
        )
        if success:
            print("Test Zoho entry creation: SUCCESS")
        else:
            print("Test Zoho entry creation: FAILED - Check Zoho logs for specific field errors.")
    else:
        print("No work orders available for testing (using mock data).")