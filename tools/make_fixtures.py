"""Generate the 5-document POC test set with exact ground truth.

doc1  native, 2-column report (Indonesian)
doc2  native invoice with a ruled table
doc3  clean 300 DPI scan (image-only PDF, no text layer)
doc4  phone photo of the invoice: perspective + rotation + noise + JPEG
doc5  mixed PDF: page 1 native, page 2 scanned image

Outputs: fixtures/*.pdf, fixtures/gt/*.txt, fixtures/gt/*_cells.json
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import fitz
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
GT = FIX / "gt"

# ---------------------------------------------------------------- content ----

DOC1_TITLE = "Laporan Tahunan Operasional 2025"
DOC1_SUB = "Divisi Pelayanan dan Distribusi"

DOC1_COL1 = [
    ("h", "Ringkasan Eksekutif"),
    (
        "p",
        "Sepanjang tahun 2025 jumlah pengiriman meningkat sebesar 18 persen "
        "dibandingkan tahun sebelumnya. Peningkatan ini terutama didorong oleh "
        "pembukaan tiga gudang baru di wilayah Jawa Barat dan perluasan jaringan "
        "mitra di Kalimantan.",
    ),
    (
        "p",
        "Biaya operasional per paket turun dari 12.000 rupiah menjadi 10.500 "
        "rupiah. Efisiensi ini berasal dari penataan ulang rute dan penggunaan "
        "kendaraan berkapasitas lebih besar.",
    ),
    ("h", "Kinerja Keuangan"),
    (
        "p",
        "Pendapatan bersih tercatat sebesar 4,7 miliar rupiah dengan margin kotor "
        "23 persen. Piutang usaha menurun karena percepatan siklus penagihan dari "
        "45 hari menjadi 30 hari.",
    ),
]

DOC1_COL2 = [
    ("h", "Rencana Tahun 2026"),
    (
        "p",
        "Perusahaan akan memprioritaskan otomatisasi proses pergudangan dan "
        "integrasi sistem pelacakan dengan para mitra logistik. Anggaran "
        "investasi disiapkan sebesar 600 juta rupiah.",
    ),
    (
        "p",
        "Rekrutmen difokuskan pada tenaga ahli data dan teknisi perawatan. "
        "Pelatihan internal dilaksanakan setiap kuartal dengan target sertifikasi "
        "minimal 70 persen karyawan.",
    ),
    ("h", "Manajemen Risiko"),
    (
        "p",
        "Risiko utama adalah ketergantungan pada satu penyedia jasa angkutan "
        "laut. Mitigasi dilakukan dengan menambah dua mitra alternatif dan "
        "menyusun kontrak jangka panjang.",
    ),
]

DOC2_HEADER = [
    "FAKTUR PENJUALAN",
    "Nomor: INV-2025-0417",
    "Tanggal: 17 November 2025",
    "Kepada: PT Sinar Abadi, Jalan Merdeka 45, Bandung",
]

DOC2_TABLE = [
    ["No", "Deskripsi Barang", "Jumlah", "Harga Satuan", "Total"],
    ["1", "Kertas A4 80 gram", "25", "52000", "1300000"],
    ["2", "Tinta printer hitam", "12", "185000", "2220000"],
    ["3", "Map plastik biru", "40", "7500", "300000"],
    ["4", "Stapler medium", "8", "47500", "380000"],
]

DOC2_FOOTER = [
    "Total: 4200000",
    "PPN 11 persen: 462000",
    "Jumlah dibayar: 4662000",
    "Pembayaran paling lambat 30 hari setelah tanggal faktur.",
]

DOC3 = [
    ("title", "Prosedur Pengiriman Barang"),
    (
        "p",
        "Setiap pengiriman wajib melalui tiga tahap pemeriksaan sebelum barang "
        "dikeluarkan dari gudang. Tahap pertama adalah verifikasi dokumen pesanan "
        "oleh staf administrasi. Tahap kedua adalah pemeriksaan fisik jumlah dan "
        "kondisi barang oleh petugas gudang.",
    ),
    ("h", "Pengemasan"),
    (
        "p",
        "Barang mudah pecah harus dibungkus dengan dua lapis pelindung dan diberi "
        "label peringatan berwarna merah. Pengemasan dilakukan pada meja kerja "
        "khusus agar berat paket dapat ditimbang secara langsung.",
    ),
    ("h", "Serah Terima"),
    (
        "p",
        "Kurir menandatangani surat jalan rangkap tiga dan menyerahkan satu "
        "lembar kepada petugas keamanan. Salinan digital diunggah ke sistem "
        "pelacakan dalam waktu satu jam setelah keberangkatan.",
    ),
]

DOC5_PAGE1 = [
    ("title", "Catatan Rapat Koordinasi"),
    (
        "p",
        "Rapat koordinasi bulanan dilaksanakan pada tanggal 12 November 2025 dan "
        "dihadiri oleh seluruh kepala unit. Agenda utama adalah evaluasi capaian "
        "triwulan ketiga serta persiapan audit internal.",
    ),
    (
        "p",
        "Kepala unit keuangan melaporkan bahwa seluruh anggaran kegiatan telah "
        "terserap sesuai rencana. Tidak ada temuan material yang perlu dibahas "
        "pada rapat ini.",
    ),
]

DOC5_PAGE2 = [
    ("h", "Tindak Lanjut"),
    (
        "p",
        "Setiap unit menyusun daftar tindak lanjut paling lambat tanggal 20 "
        "November 2025. Sekretariat akan mengompilasi daftar tersebut menjadi "
        "satu matriks dan mengirimkannya kepada seluruh peserta rapat.",
    ),
    (
        "p",
        "Pertemuan berikutnya dijadwalkan pada minggu kedua bulan Desember dengan "
        "agenda penyusunan rencana kerja tahun berikutnya.",
    ),
]


def _style(name: str, size: int, leading: int, bold: bool = False, space: int = 6):
    return ParagraphStyle(
        name,
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading,
        spaceAfter=space,
    )


def write_two_column_pdf(path: Path) -> None:
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    gutter = 10 * mm
    frame_w = (A4[0] - 40 * mm - gutter) / 2
    frame_h = A4[1] - 36 * mm
    left = Frame(20 * mm, 18 * mm, frame_w, frame_h, id="left")
    right = Frame(20 * mm + frame_w + gutter, 18 * mm, frame_w, frame_h, id="right")
    doc.addPageTemplates([PageTemplate(id="2col", frames=[left, right])])

    title_st = _style("t", 20, 24, bold=True, space=2)
    sub_st = _style("s", 11, 14, space=14)
    h_st = _style("h", 12, 15, bold=True, space=4)
    p_st = _style("p", 10, 13, space=8)

    story = [
        Paragraph(DOC1_TITLE, title_st),
        Paragraph(DOC1_SUB, sub_st),
    ]
    for kind, text in DOC1_COL1:
        if kind == "h":
            story.append(Paragraph(text, h_st))
        else:
            story.append(Paragraph(text, p_st))
    story.append(FrameBreak())
    for kind, text in DOC1_COL2:
        if kind == "h":
            story.append(Paragraph(text, h_st))
        else:
            story.append(Paragraph(text, p_st))
    doc.build(story)


def write_invoice_pdf(path: Path) -> None:
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    frame = Frame(20 * mm, 20 * mm, A4[0] - 40 * mm, A4[1] - 40 * mm, id="main")
    doc.addPageTemplates([PageTemplate(id="one", frames=[frame])])

    title_st = _style("t", 18, 22, bold=True, space=8)
    meta_st = _style("m", 11, 15, space=2)
    foot_st = _style("f", 11, 16, space=4)

    story = [
        Paragraph(DOC2_HEADER[0], title_st),
        Paragraph(DOC2_HEADER[1], meta_st),
        Paragraph(DOC2_HEADER[2], meta_st),
        Paragraph(DOC2_HEADER[3], meta_st),
        Spacer(1, 10),
    ]

    data = [[Paragraph(f"<b>{c}</b>" if r == 0 else c, meta_st) for c in row]
            for r, row in enumerate(DOC2_TABLE)]
    table = Table(data, colWidths=[14 * mm, 66 * mm, 20 * mm, 32 * mm, 34 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.9, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.90, 0.90, 0.90)),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 12))
    for line in DOC2_FOOTER:
        story.append(Paragraph(line, foot_st))
    doc.build(story)


def write_text_pdf(path: Path, blocks, title_size: int = 18) -> None:
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
    )
    frame = Frame(22 * mm, 22 * mm, A4[0] - 44 * mm, A4[1] - 44 * mm, id="main")
    doc.addPageTemplates([PageTemplate(id="one", frames=[frame])])

    title_st = _style("t", title_size, 24, bold=True, space=14)
    h_st = _style("h", 13, 17, bold=True, space=8)
    p_st = _style("p", 12, 18, space=10)

    story = []
    for kind, text in blocks:
        if kind == "title":
            story.append(Paragraph(text, title_st))
        elif kind == "h":
            story.append(Paragraph(text, h_st))
        else:
            story.append(Paragraph(text, p_st))
    doc.build(story)


def rasterize(src: Path, dpi: int = 300) -> np.ndarray:
    doc = fitz.open(src)
    pix = doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    doc.close()
    return img.copy()


def image_to_pdf(img: np.ndarray, path: Path, dpi: int = 300) -> None:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    doc = fitz.open()
    w_pt = img.shape[1] * 72.0 / dpi
    h_pt = img.shape[0] * 72.0 / dpi
    page = doc.new_page(width=w_pt, height=h_pt)
    page.insert_image(fitz.Rect(0, 0, w_pt, h_pt), stream=buf.tobytes())
    doc.save(str(path))
    doc.close()


def phone_photo(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Mild perspective warp + 2.5 deg rotation + noise + JPEG, like a desk photo."""
    h, w = img.shape[:2]
    d = 0.010
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32(
        [
            [w * d * rng.uniform(0.4, 1.0), h * d * rng.uniform(0.4, 1.0)],
            [w - w * d * rng.uniform(0.4, 1.2), h * d * rng.uniform(0.2, 0.8)],
            [w - w * d * rng.uniform(0.2, 0.9), h - h * d * rng.uniform(0.4, 1.1)],
            [w * d * rng.uniform(0.4, 1.1), h - h * d * rng.uniform(0.3, 0.9)],
        ]
    )
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        img, m, (w, h), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC
    )
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), 2.5, 1.0)
    warped = cv2.warpAffine(
        warped, rot, (w, h), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC
    )
    noise = rng.normal(0, 5.0, warped.shape)
    warped = np.clip(warped.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    warped = cv2.GaussianBlur(warped, (3, 3), 0.6)
    ok, buf = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, 72])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


def write_mixed_pdf(native: Path, scanned: Path, out: Path) -> None:
    doc = fitz.open(native)
    img_doc = fitz.open(scanned)
    doc.insert_pdf(img_doc, from_page=0, to_page=0)
    doc.save(str(out))
    doc.close()
    img_doc.close()


def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    GT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20251117)

    # doc1 native 2-column
    write_two_column_pdf(FIX / "doc1_native_2col.pdf")
    gt1 = "\n".join(
        [DOC1_TITLE, DOC1_SUB]
        + [text for _, text in DOC1_COL1]
        + [text for _, text in DOC1_COL2]
    )
    (GT / "doc1.txt").write_text(gt1, encoding="utf-8")

    # doc2 native invoice
    write_invoice_pdf(FIX / "doc2_native_invoice.pdf")
    gt2 = "\n".join(
        DOC2_HEADER
        + [" | ".join(row) for row in DOC2_TABLE]
        + DOC2_FOOTER
    )
    (GT / "doc2.txt").write_text(gt2, encoding="utf-8")
    (GT / "doc2_cells.json").write_text(
        json.dumps(DOC2_TABLE, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # doc3 clean scan: single-column page rasterised at 300 DPI, no text layer
    src3 = FIX / "_src_doc3.pdf"
    write_text_pdf(src3, DOC3)
    img3 = rasterize(src3, 300)
    image_to_pdf(img3, FIX / "doc3_scan_clean.pdf", 300)
    gt3 = "\n".join(text for _, text in DOC3)
    (GT / "doc3.txt").write_text(gt3, encoding="utf-8")

    # doc4 phone photo of the invoice table (rasterised at 250 DPI, then warped)
    img4 = rasterize(FIX / "doc2_native_invoice.pdf", 250)
    img4 = phone_photo(img4, rng)
    image_to_pdf(img4, FIX / "doc4_photo_invoice.pdf", 250)
    (GT / "doc4.txt").write_text(gt2, encoding="utf-8")
    (GT / "doc4_cells.json").write_text(
        json.dumps(DOC2_TABLE, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # doc5 mixed: native page + scanned page
    src5a = FIX / "_src_doc5_native.pdf"
    write_text_pdf(src5a, DOC5_PAGE1, title_size=17)
    src5b = FIX / "_src_doc5_scan.pdf"
    write_text_pdf(src5b, DOC5_PAGE2, title_size=17)
    img5 = rasterize(src5b, 300)
    image_to_pdf(img5, FIX / "_doc5_page2.pdf", 300)
    write_mixed_pdf(src5a, FIX / "_doc5_page2.pdf", FIX / "doc5_mixed.pdf")
    gt5 = "\n".join([t for _, t in DOC5_PAGE1] + [t for _, t in DOC5_PAGE2])
    (GT / "doc5.txt").write_text(gt5, encoding="utf-8")

    for tmp in FIX.glob("_src_*"):
        tmp.unlink()
    (FIX / "_doc5_page2.pdf").unlink()

    print("fixtures written:")
    for p in sorted(FIX.glob("*.pdf")):
        d = fitz.open(p)
        print(f"  {p.name:28s} {len(d)} page(s), text layer: {bool(d[0].get_text().strip())}")
        d.close()


if __name__ == "__main__":
    main()
