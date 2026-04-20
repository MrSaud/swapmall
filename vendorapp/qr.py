import base64
from io import BytesIO
from urllib.parse import quote_plus


def qr_image_src(text: str) -> str:
    try:
        import qrcode
    except Exception:
        return f"https://quickchart.io/qr?size=220&text={quote_plus(text)}"

    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
