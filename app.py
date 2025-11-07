# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO

# Page config: collapsed sidebar by default helps mobile users
st.set_page_config(
    page_title="Solar ROI & Savings Estimator",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# -------------------------
# Helper functions
# -------------------------
def format_currency(x):
    try:
        # handle numpy types too
        xv = float(x)
        return "₹" + f"{xv:,.2f}"
    except Exception:
        return x


def calc_bill(units, solar=0, buyback_rate=2.25):
    """
    Calculate utility bill components using slab rates.
    Returns dictionary with bill, components, net units, excess.
    """
    net_units = max(units - solar, 0)
    excess = max(solar - units, 0)

    # Slab energy charges (per unit)
    # 0-50: 3.05 ; 51-100: 3.5 ; 101-200: 4.15 ; >200: 5.2
    energy = 0.0
    if net_units <= 50:
        energy = net_units * 3.05
    elif net_units <= 100:
        energy = 50 * 3.05 + (net_units - 50) * 3.5
    elif net_units <= 200:
        energy = 50 * 3.05 + 50 * 3.5 + (net_units - 100) * 4.15
    else:
        energy = 50 * 3.05 + 50 * 3.5 + 100 * 4.15 + (net_units - 200) * 5.2

    fuel = net_units * 2.45
    fixed = 45.0
    ed = 0.15 * (fixed + energy + fuel)
    buyback = excess * buyback_rate
    bill = fixed + energy + fuel + ed - buyback
    # Keep lower bound zero (some small negative possible because buyback)
    bill = max(bill, 0.0)

    return {
        "bill": bill,
        "energy": energy,
        "fuel": fuel,
        "fixed": fixed,
        "ed": ed,
        "buyback": buyback,
        "netUnits": net_units,
        "excess": excess,
    }


def emi(principal, annual_rate_percent, months):
    """
    Standard EMI formula; returns monthly EMI payment.
    """
    if months <= 0 or principal <= 0:
        return 0.0
    r = (annual_rate_percent / 100) / 12.0
    if r == 0:
        return principal / months
    EMI = principal * r * (1 + r) ** months / ((1 + r) ** months - 1)
    return EMI


def loan_schedule(principal, annual_rate_percent, years):
    """
    Return a DataFrame with monthly EMI schedule:
    Month, EMI, Principal Paid, Interest Paid, Remaining Balance
    """
    months = int(years * 12)
    if months == 0 or principal <= 0:
        return pd.DataFrame(
            columns=[
                "Month",
                "EMI",
                "Principal Paid",
                "Interest Paid",
                "Remaining Balance",
            ]
        )
    emi_amt = emi(principal, annual_rate_percent, months)
    r = (annual_rate_percent / 100) / 12.0
    balance = principal
    rows = []
    for m in range(1, months + 1):
        interest = balance * r
        principal_component = emi_amt - interest
        # Guard against tiny negatives due to floating rounding at final payment
        if principal_component > balance:
            principal_component = balance
            emi_amt = interest + principal_component
        balance = max(balance - principal_component, 0.0)
        rows.append([m, emi_amt, principal_component, interest, balance])
    df = pd.DataFrame(
        rows,
        columns=[
            "Month",
            "EMI",
            "Principal Paid",
            "Interest Paid",
            "Remaining Balance",
        ],
    )
    return df


def to_excel_bytes(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="projection")
    return output.getvalue()


# -------------------------
# Core projection function (fixed finance logic)
# -------------------------
def generate_projection(
    unit_usage,
    solar_gen,
    install_cost,
    maint_cost,
    buyback_rate,
    projection_years,
    include_emi=False,
    loan_amount=0.0,
    loan_rate=0.0,
    loan_tenure_years=0,
):
    months = int(projection_years * 12)

    # baseline monthly bill (before solar)
    before_details = calc_bill(unit_usage, solar=0, buyback_rate=buyback_rate)
    before_bill_monthly = before_details["bill"]

    # with solar monthly bill (excluding maintenance)
    after_details = calc_bill(unit_usage, solar=solar_gen, buyback_rate=buyback_rate)
    after_bill_monthly = after_details["bill"]

    # EMI Calculation
    monthly_finance_payment = 0.0
    months_loan = 0
    loan_schedule_df = None
    if include_emi and loan_amount > 0 and loan_tenure_years > 0:
        months_loan = int(loan_tenure_years * 12)
        monthly_finance_payment = emi(loan_amount, loan_rate, months_loan)
        # prepare amortization schedule for download/display
        loan_schedule_df = loan_schedule(loan_amount, loan_rate, loan_tenure_years)

    # Build monthly cost array:
    # During loan tenure → After bill + maintenance + EMI
    # After loan ends → After bill + maintenance only
    months_arr = np.arange(1, months + 1)
    monthly_costs = np.where(
        months_arr <= months_loan,
        after_bill_monthly + maint_cost + monthly_finance_payment,
        after_bill_monthly + maint_cost,
    ).astype(float)

    # Cumulative cost with solar
    cumulative_with_solar = np.cumsum(monthly_costs)

    # If financed (EMI) there is typically no upfront investment included in monthly cost.
    # For non-financed flow include install_cost upfront in cumulative cost at month 0 (i.e. add to all months)
    if include_emi and months_loan > 0:
        cumulative_with_solar_plus_investment = cumulative_with_solar.copy()
    else:
        # add one-time upfront install cost to the cumulative cost
        cumulative_with_solar_plus_investment = cumulative_with_solar + install_cost

    # Cumulative cost without solar
    cumulative_without_solar = before_bill_monthly * months_arr

    # Savings (without - with)
    cumulative_saving = cumulative_without_solar - cumulative_with_solar_plus_investment

    # Monthly saving values shown in UI
    monthly_saving_before_loan = before_bill_monthly - (
        after_bill_monthly
        + maint_cost
        + (monthly_finance_payment if months_loan > 0 else 0.0)
    )
    monthly_saving_after_loan = before_bill_monthly - (after_bill_monthly + maint_cost)

    # Payback month (first month where cumulative saving >= 0)
    payback_month = None
    indices = np.where(cumulative_saving >= 0)[0]
    if indices.size > 0:
        payback_month = int(indices[0]) + 1  # months are 1-indexed

    # DF for graphs
    df = pd.DataFrame(
        {
            "Month": months_arr,
            "Cumulative_Cost_Without_Solar": cumulative_without_solar,
            "Cumulative_Cost_With_Solar_Plus_Investment": cumulative_with_solar_plus_investment,
            "Cumulative_Saving": cumulative_saving,
        }
    )

    # Yearly summary table (end-of-year totals)
    year_rows = []
    for y in range(1, projection_years + 1):
        m = y * 12
        year_rows.append(
            {
                "Year": y,
                "Total_Cost_Without_Solar": float(cumulative_without_solar[m - 1]),
                "Total_Cost_With_Solar_Plus_Investment": float(
                    cumulative_with_solar_plus_investment[m - 1]
                ),
                "Total_Cumulative_Saving": float(cumulative_saving[m - 1]),
            }
        )
    df_year = pd.DataFrame(year_rows)

    return {
        "monthly_before_bill": float(before_bill_monthly),
        "monthly_after_bill": float(after_bill_monthly),
        "monthly_saving": float(monthly_saving_before_loan),
        "monthly_saving_post_loan": float(monthly_saving_after_loan),
        "payback_month": payback_month,
        "payback_years": (payback_month / 12.0) if payback_month else None,
        "df_monthly": df,
        "df_yearly": df_year,
        "before_details": before_details,
        "after_details": after_details,
        "financed": include_emi,
        "monthly_finance_payment": float(monthly_finance_payment),
        "months_loan": months_loan,
        "loan_schedule_df": loan_schedule_df,
    }


# -------------------------
# Sidebar - Inputs
# -------------------------
with st.sidebar:
    st.title("Inputs")
    st.markdown("Enter your energy profile and solar system details")

    unit_usage = st.number_input(
        "Average monthly unit usage (kWh)", min_value=0, value=500, step=10
    )
    solar_gen = st.number_input(
        "Estimated solar generation (kWh/month)", min_value=0, value=350, step=10
    )
    install_cost = st.number_input(
        "Total installation cost (₹)", min_value=0, value=80000, step=1000
    )
    maint_cost = st.number_input(
        "Estimated monthly maintenance (₹)", min_value=0, value=250, step=50
    )
    buyback_rate = st.number_input(
        "Net-metering buyback rate (₹ / unit)",
        min_value=0.0,
        value=2.25,
        step=0.25,
        format="%.2f",
    )

    projection_years = st.selectbox(
        "Projection horizon (years)", options=[5, 10, 15, 20, 25], index=0
    )

    st.markdown("---")
    include_emi = st.checkbox(
        "Compare financing (EMI) instead of upfront payment?", value=False
    )
    loan_amount = 0.0
    loan_rate = 0.0
    loan_tenure = 0
    if include_emi:
        loan_amount = st.number_input(
            "Loan amount (₹) — if financing, typically equal to install cost",
            min_value=0,
            value=int(install_cost),
            step=1000,
        )
        loan_rate = st.number_input(
            "Loan annual interest rate (%)",
            min_value=0.0,
            value=10.0,
            step=0.1,
            format="%.2f",
        )
        loan_tenure = st.selectbox(
            "Loan tenure (years)", options=[1, 2, 3, 4, 5, 7, 10], index=1
        )

    st.markdown("---")
    show_monthly_table = st.checkbox("Show monthly table (may be long)", value=False)
    download_excel = st.checkbox("Provide Excel download for projection", value=True)
    st.markdown("Built from your React design — adapted to Streamlit.")

# -------------------------
# Main UI
# -------------------------
st.title("☀️ Solar ROI & Savings Estimator")
st.write(
    "Estimate your monthly savings and investment payback for a proposed solar installation."
)

# Summary top cards (use single-column layout on narrow screens; Streamlit will stack)
top_cols = st.columns([1, 1, 1, 1])
top_cols[0].metric("Avg monthly consumption", f"{unit_usage:,.0f} kWh")
top_cols[1].metric("Est. solar generation", f"{solar_gen:,.0f} kWh / month")
top_cols[2].metric("Buyback rate", f"₹{buyback_rate:.2f} / unit")
top_cols[3].metric("Install cost", format_currency(install_cost))

# Action button inside a narrow container for mobile
calc_col = st.container()
with calc_col:
    if st.button("Calculate Savings & ROI"):
        with st.spinner("Calculating..."):
            res = generate_projection(
                unit_usage=unit_usage,
                solar_gen=solar_gen,
                install_cost=install_cost,
                maint_cost=maint_cost,
                buyback_rate=buyback_rate,
                projection_years=projection_years,
                include_emi=include_emi,
                loan_amount=loan_amount,
                loan_rate=loan_rate,
                loan_tenure_years=loan_tenure,
            )

        # Summary metrics (stack-friendly)
        s_cols = st.columns(4)
        s_cols[0].metric(
            "Current monthly bill", format_currency(res["monthly_before_bill"])
        )
        s_cols[1].metric(
            "Net monthly bill (inc. maint.)",
            format_currency(res["monthly_after_bill"] + maint_cost),
        )
        s_cols[2].metric(
            "Monthly saving (if positive)", format_currency(res["monthly_saving"])
        )
        payback_text = "Not possible"
        if res["payback_month"]:
            payback_text = (
                f"{res['payback_month']} months (~{res['payback_years']:.2f} years)"
            )
        s_cols[3].metric("Payback period (approx)", payback_text)

        st.markdown("---")

        # Detailed bill comparison (in an expander so mobile users can collapse)
        with st.expander("Detailed Monthly Bill Comparison (unit-level)"):
            before = res["before_details"]
            after = res["after_details"]
            comp_df = pd.DataFrame(
                {
                    "Component": [
                        "Net Consumption (Units)",
                        "Energy Charges (₹)",
                        "Fuel & Surcharges (₹)",
                        "Buyback Credit (₹)",
                        "Fixed Charge (₹)",
                        "ED (₹)",
                        "Utility Bill (₹) - before maintenance",
                    ],
                    "Without Solar": [
                        before["netUnits"],
                        before["energy"],
                        before["fuel"],
                        0.0,
                        before["fixed"],
                        before["ed"],
                        res["monthly_before_bill"],
                    ],
                    "With Solar": [
                        after["netUnits"],
                        after["energy"],
                        after["fuel"],
                        -after["buyback"],
                        after["fixed"],
                        after["ed"],
                        res["monthly_after_bill"],
                    ],
                }
            )
            comp_df["Impact (Without - With)"] = (
                comp_df["Without Solar"] - comp_df["With Solar"]
            )
            comp_df_display = comp_df.copy()
            for col in ["Without Solar", "With Solar", "Impact (Without - With)"]:
                comp_df_display[col] = comp_df_display[col].apply(
                    lambda x: (
                        format_currency(x)
                        if isinstance(x, (int, float, np.number))
                        else x
                    )
                )
            st.table(comp_df_display)

        st.markdown("---")

        # Plot: Cumulative cost comparison
        st.subheader(f"Cumulative Cost Comparison — {projection_years} years")
        dfm = res["df_monthly"]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=dfm["Month"],
                y=dfm["Cumulative_Cost_Without_Solar"],
                mode="lines",
                name="Cost Without Solar (Cumulative)",
                line=dict(width=2, dash="dash"),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=dfm["Month"],
                y=dfm["Cumulative_Cost_With_Solar_Plus_Investment"],
                mode="lines",
                name="Cost With Solar (Bills + Investment)",
                line=dict(width=3),
            )
        )
        # layout tweaks
        fig.update_layout(
            xaxis_title="Months",
            yaxis_title="Cumulative Cost (₹)",
            hovermode="x unified",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            margin=dict(t=40, b=40, l=40, r=10),
        )
        fig.update_yaxes(tickformat=",.0f")

        # Use container width (responsive)
        st.plotly_chart(fig, use_container_width=True, height=380)

        # Payback explanation
        st.info(
            (
                "Payback point: "
                f"{payback_text}. "
                "If 'Not possible', the financed/maintenance cost is higher than the savings with the current inputs."
            )
        )

        # Yearly summary (in expander)
        with st.expander("Yearly Summary"):
            dfy = res["df_yearly"].copy()
            dfy_display = dfy.copy()
            dfy_display["Total_Cost_Without_Solar"] = dfy_display[
                "Total_Cost_Without_Solar"
            ].apply(format_currency)
            dfy_display["Total_Cost_With_Solar_Plus_Investment"] = dfy_display[
                "Total_Cost_With_Solar_Plus_Investment"
            ].apply(format_currency)
            dfy_display["Total_Cumulative_Saving"] = dfy_display[
                "Total_Cumulative_Saving"
            ].apply(format_currency)
            st.table(dfy_display)

        # Monthly table (optional)
        if show_monthly_table:
            with st.expander(
                f"Monthly Projection ({projection_years*12} months shown; full CSV/XLSX downloadable)"
            ):
                dfm_display = dfm.copy()
                dfm_display["Cumulative_Cost_Without_Solar"] = dfm_display[
                    "Cumulative_Cost_Without_Solar"
                ].apply(format_currency)
                dfm_display["Cumulative_Cost_With_Solar_Plus_Investment"] = dfm_display[
                    "Cumulative_Cost_With_Solar_Plus_Investment"
                ].apply(format_currency)
                dfm_display["Cumulative_Saving"] = dfm_display[
                    "Cumulative_Saving"
                ].apply(format_currency)
                st.dataframe(dfm_display.head(projection_years*12), use_container_width=True)

        # Download buttons in a compact horizontal layout
        csv = res["df_monthly"].to_csv(index=False)
        dl_cols = st.columns([1, 1, 1])
        dl_cols[0].download_button(
            label="Download monthly CSV",
            data=csv,
            file_name=f"solar_projection_{projection_years}y.csv",
            mime="text/csv",
        )
        if download_excel:
            excel_bytes = to_excel_bytes(res["df_monthly"])
            dl_cols[1].download_button(
                label="Download monthly Excel",
                data=excel_bytes,
                file_name=f"solar_projection_{projection_years}y.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        # Automated insights & suggestions (compact)
        st.markdown("---")
        st.subheader("Automated Insights & Suggestions")
        insights = []
        if res["monthly_saving"] <= 0:
            insights.append(
                "Inputs do not show positive monthly savings (after maintenance). Re-check solar size, maintenance, or finance costs."
            )
        else:
            insights.append(
                f"Estimated monthly saving (during loan period if financed) is {format_currency(res['monthly_saving'])}."
            )
        suggested_solar = round(
            unit_usage * 0.6
        )  # naive suggestion: 60% of consumption
        insights.append(
            f"Rule-of-thumb sizing: consider a solar system that generates ~{suggested_solar} kWh/month (~{round(suggested_solar/30,1)} kWh/day)."
        )
        if include_emi:
            monthly_emi = res["monthly_finance_payment"]
            insights.append(
                f"Monthly EMI: {format_currency(monthly_emi)}. If EMI > monthly saving, financing increases monthly outgoing."
            )

        for it in insights:
            st.write("- ", it)

        # EMI schedule (if financed) inside expander to keep mobile UI small
        if include_emi and res.get("loan_schedule_df") is not None:
            with st.expander("EMI / Loan Amortization Schedule (first 24 months)"):
                schedule_df = res["loan_schedule_df"].copy()
                schedule_show = schedule_df.head(24).copy()
                for c in [
                    "EMI",
                    "Principal Paid",
                    "Interest Paid",
                    "Remaining Balance",
                ]:
                    schedule_show[c] = schedule_show[c].apply(format_currency)
                st.dataframe(schedule_show, use_container_width=True)
                # Download full schedule
                st.download_button(
                    label="Download full EMI schedule (Excel)",
                    data=to_excel_bytes(schedule_df),
                    file_name="emi_schedule.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

        st.success("Calculation complete.")
    else:
        st.info(
            "Adjust inputs in the sidebar and click **Calculate Savings & ROI** to generate projections."
        )
