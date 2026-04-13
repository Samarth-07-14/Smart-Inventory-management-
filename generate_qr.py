#!/usr/bin/env python3
"""
QR Code Generator for Warehouse Testing
Generates printable QR codes for RACK and BOX labels.

Usage:
  python generate_qr.py

Outputs PNG files in a 'qr_codes/' folder.
"""

import os

try:
    import qrcode
except ImportError:
    print("Install qrcode: pip install qrcode[pil]")
    exit(1)

from PIL import Image, ImageDraw, ImageFont


OUTPUT_DIR = "qr_codes"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def generate_qr(data: str, filename: str, label: str = None):
    """Generate a QR code image with a label below it."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_w, qr_h = qr_img.size

    # Add label text below QR
    label_text = label or data
    label_height = 50
    final = Image.new("RGB", (qr_w, qr_h + label_height), "white")
    final.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(final)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        except Exception:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), label_text, font=font)
    tw = bbox[2] - bbox[0]
    tx = (qr_w - tw) // 2
    ty = qr_h + 8
    draw.text((tx, ty), label_text, fill="black", font=font)

    filepath = os.path.join(OUTPUT_DIR, filename)
    final.save(filepath)
    print(f"  Created: {filepath}  [{data}]")


if __name__ == "__main__":
    print("=" * 50)
    print("  Warehouse QR Code Generator")
    print("=" * 50)
    print()

    # ── Generate RACK QR codes ──
    print("Generating RACK codes:")
    for i in range(1, 6):
        rack_id = f"RACK_{i:02d}"
        generate_qr(rack_id, f"rack_{i:02d}.png", rack_id)

    print()

    # ── Generate BOX QR codes ──
    print("Generating BOX codes:")
    for i in range(1, 11):
        box_id = f"BOX_{i:02d}"
        generate_qr(box_id, f"box_{i:02d}.png", box_id)

    print()

    # ── Generate GROUND QR codes ──
    print("Generating GROUND codes:")
    for i in range(1, 3):
        gnd_id = f"GROUND_{i:02d}"
        generate_qr(gnd_id, f"ground_{i:02d}.png", gnd_id)

    print()
    print(f"All QR codes saved to: {os.path.abspath(OUTPUT_DIR)}/")
    print("Print these and stick them on racks/boxes for testing.")
    print()

    # ── Generate a print sheet (all QRs on one page) ──
    print("Generating combined print sheet...")
    all_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.endswith(".png")
                        and f != "print_sheet.png"])
    if all_files:
        imgs = [Image.open(os.path.join(OUTPUT_DIR, f)) for f in all_files]
        # Grid layout: 4 columns
        cols = 4
        rows_needed = (len(imgs) + cols - 1) // cols
        
        cell_w = max(img.width for img in imgs) + 20
        cell_h = max(img.height for img in imgs) + 20
        
        sheet = Image.new("RGB", (cols * cell_w, rows_needed * cell_h), "white")
        
        for idx, img in enumerate(imgs):
            r, c = divmod(idx, cols)
            x = c * cell_w + (cell_w - img.width) // 2
            y = r * cell_h + (cell_h - img.height) // 2
            sheet.paste(img, (x, y))
        
        sheet_path = os.path.join(OUTPUT_DIR, "print_sheet.png")
        sheet.save(sheet_path)
        print(f"  Print sheet: {sheet_path}")
    
    print("\nDone!")
