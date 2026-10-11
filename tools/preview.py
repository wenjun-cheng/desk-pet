"""Draws the README's preview (docs/preview.png and docs/preview.zh-CN.png): the pet at the bottom of the screen,
over Autodesk Fusion, answering what was asked in its bubble. The background (tools/preview-fusion.png) is Fusion
after the pet's Claude built that bracket there; the pet and its bubble are drawn by the pet's own code. Run from
the repository: python tools/preview.py (its windows flash by at the bottom left of the screen: drawn off screen,
Qt wouldn't have the system's fonts)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DESKPET_DATA"] = tempfile.mkdtemp()

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from deskpet import desktop, face, paths  # noqa: E402
from deskpet.chat import Chat  # noqa: E402
from deskpet.pet import Pet  # noqa: E402
from deskpet.settings import Settings  # noqa: E402
from deskpet.strings import set_language, tr  # noqa: E402

TEXT = {
    "en": {"asked": "“Make an L-bracket here: 60 mm wide, 5 mm thick, two M5 holes in the base”",
           "reply": "The L-bracket is done: 60 mm wide, 5 mm thick, both legs 40 mm long, two M5 holes in the base."},
    "zh": {"asked": "“在这里建一个 L 形支架：宽 60 mm、厚 5 mm，底板上两个 M5 孔”",
           "reply": "L 形支架已经做好：宽 60 mm、厚 5 mm，两条边各 40 mm 长，底板上有两个 M5 孔。"},
}
BACKGROUND = Path(__file__).with_name("preview-fusion.png")  # (in screen pixels: SCALE per point)
SCALE = 2
GAP, CAPTION_H, PET_X = 16, 34, 40


def main():
    qapp = QApplication(sys.argv)
    for font in (paths.ASSETS / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    desktop.give_focus = desktop.restore_focus = lambda *_: None
    screen = QPixmap(str(BACKGROUND))
    screen.setDevicePixelRatio(SCALE)
    frame = QRectF(GAP, GAP, screen.width() / SCALE, screen.height() / SCALE)
    for lang, text in TEXT.items():
        set_language(lang)
        settings = Settings(Path(os.environ["DESKPET_DATA"]) / "settings.json")
        settings["x"] = 120  # (the bubble reaches right)
        pet = Pet(settings)
        chat = Chat(pet, settings, make_session=lambda *_: None)
        pet.frame_timer.stop()
        pet.check_timer.stop()  # (no falling asleep)
        pet.blink_at = time.monotonic() + 1000
        pet.glance_at = time.monotonic() + 1000
        pet.sink = pet.OUT
        chat._say(text["reply"], "reply")
        chat.bubble.open_input(tr("follow_up"), take_focus=False)
        QTest.qWait(400)  # (the bubble pops up)

        canvas = QPixmap(int((frame.width() + 2 * GAP) * SCALE), int((frame.bottom() + GAP + CAPTION_H) * SCALE))
        canvas.setDevicePixelRatio(SCALE)
        canvas.fill(QColor("#1e2129"))
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.save()
        p.setClipRect(frame)
        p.drawPixmap(frame.topLeft(), screen)
        # the pet's window, with the screen's bottom edge at EDGE in it
        origin = QPointF(frame.left() + PET_X, frame.bottom() - pet.EDGE)
        offset = chat.bubble.pos() - pet.pos()
        chat.bubble.render(p, QPoint(int(origin.x() + offset.x()), int(origin.y() + offset.y())))
        p.translate(origin)
        p.setClipRect(QRectF(0, 0, pet.SIZE, pet.EDGE))
        look = pet._look(time.monotonic())
        look.eyes = "happy"
        face.draw(p, look)
        p.restore()

        caption_font = QFont()
        caption_font.setFamilies(["Segoe UI", "Helvetica Neue", "Microsoft YaHei UI", "PingFang SC"])
        caption_font.setPixelSize(15)
        p.setFont(caption_font)
        p.setPen(QColor(215, 215, 222))
        p.drawText(QRectF(frame.left(), frame.bottom() + 9, frame.width(), CAPTION_H),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text["asked"])
        p.end()
        out = ROOT / "docs" / ("preview.png" if lang == "en" else "preview.zh-CN.png")
        canvas.save(str(out))
        print("saved", out)
        chat.bubble.close()
        pet.close()
    qapp.quit()


if __name__ == "__main__":
    main()
