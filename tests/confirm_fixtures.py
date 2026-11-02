"""Synthetic replicas of the firm's confirmation layouts (values are invented).

Each builder mirrors the structure of one real October 2026 family, so the parsers can be
tested without committing real confirmations. "Kingston Marketing Limited" is the firm.
"""

from __future__ import annotations

import io

from doc_fixtures import build_msg, build_pdf

US = "Kingston Marketing Limited"

MODERN_HEADERS = ["Trade Number", "State", "Product", "Location", "Pipeline/Terminal",
                  "Price Basis", "Term", "Term Start", "Term End", "Price", "Volume",
                  "Unit of Measure", "Executed Timestamp", "Last Updated Timestamp",
                  "Trade Type", "Side", "Bid Trader", "Bid Legal Name", "Bid Address",
                  "Bid Commission", "Offer Trader", "Offer Legal Name", "Offer Address",
                  "Offer Commission", "Spread Trade Number", "Apportionment Protected",
                  "Clearing ID", "Settlement Currency", "Contract Terms", "GT&C", "Notes",
                  "In Index", "Click & Trade", "Product Type", "Credit Facilitation", "Broker"]


def modern_xlsx(*, number=90001, side="Buy", product="MID", price=-8.75, volume=2500.0,
                term="NOV-26", start="2026-11-01", end="2026-11-30",
                executed="2026-10-02 08:59:46 AM", updated="2026-10-02 09:09:46 AM",
                cpty="SYN Refining Inc.", our_trader="Libby Smith", their_trader="SYN Trader",
                currency="USD", state="Finalized") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Report"
    ws.append(MODERN_HEADERS)
    buy = side == "Buy"
    row = {"Trade Number": number, "State": state, "Product": product, "Location": "Cromer",
           "Pipeline/Terminal": "Enb T@S", "Price Basis": "WTI CMA", "Term": term,
           "Term Start": start, "Term End": end, "Price": price, "Volume": volume,
           "Unit of Measure": "m3/month", "Executed Timestamp": executed,
           "Last Updated Timestamp": updated, "Trade Type": "Outright", "Side": side,
           "Bid Trader": our_trader if buy else their_trader,
           "Bid Legal Name": US if buy else cpty,
           "Offer Trader": their_trader if buy else our_trader,
           "Offer Legal Name": cpty if buy else US, "Settlement Currency": currency,
           "Product Type": "Physical", "Broker": "Modern Commodities Inc"}
    ws.append([row.get(h) for h in MODERN_HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def ox_pdf(*, deal="2099001", buyer="SYN Resources Inc.", seller=US, price="-$6.10 USD / BBL",
           qty="2,500 M3 / Month", notes="Kingston Paper; BP GTCs; CAD",
           extra_address_line=False) -> bytes:
    lines = ["BROKERED TRADE CONFIRMATION", "Trade Date: October 05, 2026",
             "Trade Execution Time: 9:40 AM", "Issue Date: October 05, 2026",
             f"Transaction ID: {deal}", f"Confirmation #: 051026_{deal}_S", "PARTICIPANTS",
             f"Buyer: {buyer}", "3800 525 - 8th Ave SW", "Calgary,Alberta,T2P 1G1"]
    if extra_address_line:
        lines.append("Floor 2")
    lines += [f"Seller: {seller}", "308 - 4th Avenue SW", "Calgary,AB,T2P 0H7",
              "Trader: SYN Buyer Trader", "Phone:", "Trader: Libby Smith",
              "As agent on your behalf, we have completed and confirm the following:",
              "Deal Type: LSB Enb", f"Price: {price}", "Start Date: November 01, 2026",
              f"Quantity: {qty}", "Grade Type: SOUR", "End Date: November 30, 2026",
              "Delivery Point: Cromer, MB (Enbridge)",
              "Buyer Pays: Calendar average of the New York Mercantile Exchange WTI settlement",
              f"Notes: {notes}", "Please notify us of any errors immediately One Exchange Corp."]
    return build_pdf([lines])


def marex_pdf(*, ref="119999001", seller=US, buyer="SYN Products Inc", sell_trader="Tyler Demers.",
              price="-6.50 USD/Barrel", volume="5,000m3", contract="Nov-26") -> bytes:
    pairs = [("Marex Reference", ref), ("Deal Date", "05-Oct-26 14:53:13"),
             ("Confirm Sent Date", "05-Oct-26 14:54:39"), ("Seller", seller),
             ("Sell Trader", sell_trader), ("Sell Broker", "SYN Broker"), ("Buyer", buyer),
             ("Buy Trader", "SYN Buy Trader"), ("Market", "Canadian Crude"), ("Price", price),
             ("Volume", volume), ("Total Volume", "31,464.35 Barrels"), ("Location", "Cromer"),
             ("Pipeline", "Enb TAS"), ("Index", "WTI CMA"), ("Instrument", "LSB ENB"),
             ("Contract", contract), ("Settlement Currency", "USD")]
    lines = ["Thank you for choosing Marex Spectron International Limited Canada Branch"]
    for k, v in pairs:
        lines += [k, v]
    return build_pdf([lines])


def marex_xls(*, ref=119999001.0, price=-6.5, volume=5000.0) -> bytes:
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Summary")
    ws.write(0, 0, f"{US} (03-Oct-26 - 05-Oct-26 11:59 For 23:00 Schedule)")
    headers = ["Reference", "Deal Date", "Buy Company", "Buy Trader", "Sell Company",
               "Sell Trader", "Market", "Price", "Volume", "Total Volume", "Instrument",
               "Pipeline", "Index", "Settlement Currency", "Contract By", "Terms And Conditions"]
    values = [ref, "05-Oct-2026 14:53:13", "SYN Oil on behalf of SYN Products Inc",
              "SYN Buy Trader", US, "Tyler Demers", "Canadian Crude", price, volume, 31464.35,
              "LSB ENB - Nov-26", "Enb TAS", "WTI CMA", "USD", "SYN Products Inc",
              "SYN Products Inc"]
    for c, h in enumerate(headers):
        ws.write(1, c, h)
        ws.write(2, c, values[c])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def calrock_msg(*, trade_id="82999001", buyer=US, seller="SYN Energy Marketing Inc.",
                buyer_trader="Tyler Demers", product="WCS - HAR HSK - Nov26", price="-24.85",
                volume="5000 cbm", settle="USD", forwarded=False) -> bytes:
    body = ("Trade confirmation\n------------------------\n\n"
            f"Product                 :    {product}\n"
            f"Price(USD/bbl)   :    {price}\n"
            f"Volume                 :    {volume}\n"
            "Term Start            :    November  01, 2026\n"
            "Term End              :    November  30, 2026\n"
            "Hub                         :    Hardisty - Husky\n"
            f"Trade ID                 :    {trade_id}\n"
            "Trade Time           :    October   05, 2026 10:05\n"
            "Index                      :    WTI CMA\n"
            f"Settle Currency   :    {settle}\n\n"
            f"Buyer Company  :    {buyer}\n"
            f"Buyer Trader        :    {buyer_trader}\n"
            f"Seller Company   :    {seller}\n"
            "Seller Trader         :    SYN Seller Trader\n\nCalRock Brokers Inc.\n")
    if forwarded:
        body = ("Bot 5k m3 more Nov WCS.\n\n-----Original Message-----\n"
                "From: trade_confirmation@calrockbrokers.example\n"
                "Subject: CalRock Energy trade confirmation\n\n" + body)
    return build_msg(subject=f"CalRock Energy trade confirmation: #{trade_id}", body=body,
                     sender_smtp="trade_confirmation@calrockbrokers.example")


def isda_pdf(*, ref="167000001 / 181000001", fixed_payer="SYN Bank of Canada",
             floating_payer=US, fixed_price="CAD 106.12 per BBL", effective="October 1, 2027",
             termination="October 31, 2027", payments_in="CAD") -> bytes:
    p1 = [f"Our Reference Number: {ref}", "Montreal, October 2, 2026", US,
          "Confirmation of a", "COMMODITY SWAP TRANSACTION - CASH", "SETTLED", "Between",
          "SYN Bank of Canada", "and", US]
    p2 = ["1. General Terms:", "Trade Date: October 2, 2026", f"Effective Date: {effective}",
          f"Termination Date: {termination}", "Commodity: Crude Oil",
          "Total Notional Quantity: 10,000.00 BBL", "Fixed Amount Details:",
          f"Fixed Price Payer: {fixed_payer}", f"Fixed Price: {fixed_price}",
          "Floating Amount Details:", f"Floating Price Payer: {floating_payer}",
          "Commodity Reference Price: OIL-WTI-NYMEX"]
    p3 = ["3. Account Details:", f"Payments in : {payments_in}"]
    return build_pdf([p1, p2, p3])
