# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO

# Page config
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
        xv = float(x)
        return "₹" + f"{xv:,.0f}"
    except Exception:
        return x


def calc_bill(units, solar=0, buyback_rate=2.25):
    net_units = max(units - solar, 0)
    excess = max(solar - units, 0)

    # Slab rates
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
    if months <= 0 or principal <= 0:
        return 0.0
    r = (annual_rate_percent / 100) / 12.0
    if r == 0:
        return principal / months
    EMI = principal * r * (1 + r) ** months / ((1 + r) ** months - 1)
    return EMI


def loan_schedule(principal, annual_rate_percent, years):
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
# Core projection function
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

    before_details = calc_bill(unit_usage, solar=0, buyback_rate=buyback_rate)
    before_bill_monthly = before_details["bill"]

    after_details = calc_bill(unit_usage, solar=solar_gen, buyback_rate=buyback_rate)
    after_bill_monthly = after_details["bill"]

    monthly_finance_payment = 0.0
    months_loan = 0
    loan_schedule_df = None
    if include_emi and loan_amount > 0 and loan_tenure_years > 0:
        months_loan = int(loan_tenure_years * 12)
        monthly_finance_payment = emi(loan_amount, loan_rate, months_loan)
        loan_schedule_df = loan_schedule(loan_amount, loan_rate, loan_tenure_years)

    months_arr = np.arange(1, months + 1)
    monthly_costs = np.where(
        months_arr <= months_loan,
        after_bill_monthly + maint_cost + monthly_finance_payment,
        after_bill_monthly + maint_cost,
    ).astype(float)

    cumulative_with_solar = np.cumsum(monthly_costs)

    if include_emi and months_loan > 0:
        cumulative_with_solar_plus_investment = cumulative_with_solar.copy()
    else:
        cumulative_with_solar_plus_investment = cumulative_with_solar + install_cost

    cumulative_without_solar = before_bill_monthly * months_arr
    cumulative_saving = cumulative_without_solar - cumulative_with_solar_plus_investment

    monthly_saving_before_loan = before_bill_monthly - (
        after_bill_monthly
        + maint_cost
        + (monthly_finance_payment if months_loan > 0 else 0.0)
    )
    monthly_saving_after_loan = before_bill_monthly - (after_bill_monthly + maint_cost)

    payback_month = None
    indices = np.where(cumulative_saving >= 0)[0]
    if indices.size > 0:
        payback_month = int(indices[0]) + 1

    # Final loan payoff info
    final_payment_month = None
    total_paid_over_loan = None
    if include_emi and loan_schedule_df is not None and months_loan > 0:
        final_payment_month = loan_schedule_df["Month"].max()
        total_paid_over_loan = loan_schedule_df["EMI"].sum()

    df = pd.DataFrame(
        {
            "Month": months_arr,
            "Cumulative_Cost_Without_Solar": cumulative_without_solar,
            "Cumulative_Cost_With_Solar_Plus_Investment": cumulative_with_solar_plus_investment,
            "Cumulative_Saving": cumulative_saving,
        }
    )

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
        "final_payment_month": final_payment_month,
        "total_paid_over_loan": total_paid_over_loan,
    }


# -------------------------
# Sidebar Inputs
# -------------------------
with st.sidebar:
    st.title("Inputs")
    st.markdown("Enter your energy profile and solar system details")

    unit_usage = st.number_input(
        "Average monthly unit usage (kWh)", min_value=0, value=250, step=10
    )
    solar_gen = st.number_input(
        "Estimated solar generation (kWh/month)", min_value=0, value=350, step=10
    )
    install_cost = st.number_input(
        "Total installation cost (₹)", min_value=0, value=80000, step=1000
    )
    maint_cost = st.number_input(
        "Estimated monthly maintenance (₹)", min_value=0, value=450, step=50
    )
    buyback_rate = st.number_input(
        "Net-metering buyback rate (₹ / unit)",
        min_value=0.0,
        value=2.25,
        step=0.25,
        format="%.2f",
    )

    projection_years = st.selectbox(
        "Projection horizon (years)", options=[5, 10, 15, 20, 25], index=1
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
            "Loan amount (₹)", min_value=0, value=int(install_cost), step=1000
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
st.write(
    "Developed by Karm Patel"
)

top_cols = st.columns([1, 1, 1, 1])
top_cols[0].metric("Avg monthly consumption", f"{unit_usage:,.0f} Unit")
top_cols[1].metric("Est. monthly solar generation", f"{solar_gen:,.0f} Unit")
top_cols[2].metric("Buyback rate", f"₹{buyback_rate:.2f} / unit")
top_cols[3].metric("Install cost after subsidy", format_currency(install_cost))

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

        # Summary metrics
        s_cols = st.columns(4)
        s_cols[0].metric(
            "Current monthly bill", format_currency(res["monthly_before_bill"])
        )
        s_cols[1].metric(
            "Net monthly bill (inc. maint.)",
            format_currency(res["monthly_after_bill"] + maint_cost),
        )
        s_cols[2].metric(
            "Monthly saving", format_currency(res["monthly_saving"])
        )
        payback_text = "Not possible"
        if res["payback_month"]:
            payback_text = (
                f"{res['payback_month']} months (~{res['payback_years']:.1f} years)"
            )
        s_cols[3].metric("Payback period (approx)", payback_text.split("(", 1)[0])

        # Final loan payoff info
        if include_emi and res["final_payment_month"]:
            st.info(
                f"💰 Loan fully paid in month {res['final_payment_month']}, total payments: "
                f"{format_currency(res['total_paid_over_loan'])} (install cost + interest)."
            )

        st.markdown("---")

        # Detailed Monthly Bill Comparison
        with st.expander("Detailed Monthly Bill Comparison (unit-level)"):
            before = res["before_details"]
            after = res["after_details"]
            # With Solar, keep buyback as negative credit and allow total bill to go negative
            comp_df = pd.DataFrame(
                {
                    "Component": [
                        "Net Consumption (Units)",
                        "Energy Charges (₹)",
                        "Fuel & Surcharges (₹)",
                        "Buyback Credit (₹) (negative = credit)",
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
                        res["monthly_before_bill"],  # same as before
                    ],
                    "With Solar": [
                        after["netUnits"],
                        after["energy"],
                        after["fuel"],
                        -after["buyback"],  # negative = credit
                        after["fixed"],
                        after["ed"],
                        # Keep bill as-is including negative if buyback > total charges
                        after["fixed"] + after["energy"] + after["fuel"] + after["ed"] - after["buyback"],
                    ],
                }
            )

            comp_df["Impact (Without - With)"] = (
                comp_df["Without Solar"] - comp_df["With Solar"]
            )
            for col in ["Without Solar", "With Solar", "Impact (Without - With)"]:
                comp_df[col] = comp_df[col].apply(format_currency)
            st.table(comp_df)

        # Cumulative Cost Plot
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
        fig.update_layout(
            xaxis=dict(fixedrange=True),
            yaxis=dict(fixedrange=True),
            dragmode=False,  # disables pan/zoom
            hovermode=False,
            showlegend=True,
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            margin=dict(t=40, b=40, l=40, r=10),
        )
        fig.update_yaxes(tickformat=",.0f")
        st.plotly_chart(fig, use_container_width=True, height=380)

        st.info(
            f"Payback point: {payback_text}. If 'Not possible', the financed/maintenance cost is higher than savings."
        )

        # Yearly summary
        with st.expander("Yearly Summary"):
            dfy = res["df_yearly"].copy()
            for col in [
                "Total_Cost_Without_Solar",
                "Total_Cost_With_Solar_Plus_Investment",
                "Total_Cumulative_Saving",
            ]:
                dfy[col] = dfy[col].apply(format_currency)
            st.table(dfy)

        # Optional monthly table
        if show_monthly_table:
            with st.expander(f"Monthly Projection ({projection_years*12} months)"):
                dfm_display = dfm.copy()
                for col in [
                    "Cumulative_Cost_Without_Solar",
                    "Cumulative_Cost_With_Solar_Plus_Investment",
                    "Cumulative_Saving",
                ]:
                    dfm_display[col] = dfm_display[col].apply(format_currency)
                st.dataframe(
                    dfm_display.head(projection_years * 12), use_container_width=True
                )

        # Download buttons
        csv = res["df_monthly"].to_csv(index=False)
        dl_cols = st.columns([1, 1, 1])
        dl_cols[0].download_button(
            "Download monthly CSV",
            csv,
            file_name=f"solar_projection_{projection_years}y.csv",
            mime="text/csv",
        )
        if download_excel:
            excel_bytes = to_excel_bytes(res["df_monthly"])
            dl_cols[1].download_button(
                "Download monthly Excel",
                excel_bytes,
                file_name=f"solar_projection_{projection_years}y.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        # Insights
        st.markdown("---")
        st.subheader("Automated Insights & Suggestions")
        insights = []
        if res["monthly_saving"] <= 0:
            insights.append(
                "Inputs do not show positive monthly savings. Re-check solar size, maintenance, or finance costs."
            )
        else:
            insights.append(
                f"Estimated monthly saving (during loan period if financed): {format_currency(res['monthly_saving'])}."
            )
        suggested_solar = round(unit_usage * 0.6)
        insights.append(
            f"Rule-of-thumb sizing: consider solar ~{suggested_solar} kWh/month (~{round(suggested_solar/30,1)} kWh/day)."
        )
        if include_emi:
            insights.append(
                f"Monthly EMI: {format_currency(res['monthly_finance_payment'])}. If EMI > monthly saving, financing increases outgoing."
            )

        for it in insights:
            st.write("- ", it)

        # EMI schedule
        if include_emi and res.get("loan_schedule_df") is not None:
            with st.expander("EMI / Loan Schedule (first 24 months)"):
                schedule_df = res["loan_schedule_df"].head(24).copy()
                for c in [
                    "EMI",
                    "Principal Paid",
                    "Interest Paid",
                    "Remaining Balance",
                ]:
                    schedule_df[c] = schedule_df[c].apply(format_currency)
                st.dataframe(schedule_df, use_container_width=True)
                st.download_button(
                    "Download full EMI schedule (Excel)",
                    data=to_excel_bytes(res["loan_schedule_df"]),
                    file_name="emi_schedule.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

        st.success("Calculation complete.")
    else:
        st.info(
            "Adjust inputs in the sidebar and click **Calculate Savings & ROI** to generate projections."
        )
