from pathlib import Path
import fitz
from lineaforge.batch import BatchItem, run_batch


def test_ten_file_batch_continues_after_corrupt_and_encrypted(tmp_path):
    items = []
    for index in range(10):
        source = tmp_path/f"input-{index}.pdf"
        if index == 8:
            source.write_bytes(b"corrupt-not-pdf")
        else:
            with fitz.open() as document:
                page = document.new_page(width=100,height=100)
                page.draw_line((5,5),(80,80))
                kwargs = dict(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret") if index == 9 else {}
                document.save(source, **kwargs)
        items.append(BatchItem(source, tmp_path/f"output-{index}.dxf"))
    events = []
    summary = run_batch(items, callback=lambda *args: events.append(args))
    assert summary["done"] == 8 and summary["failed"] == 2
    assert "encrypted" in items[9].error.lower()
    assert "pdf" in items[8].error.lower()
    assert events
