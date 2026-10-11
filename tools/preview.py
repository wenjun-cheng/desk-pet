"""Draws the README's preview (docs/preview.png and docs/preview.zh-CN.png): the bottom of a screen with the pet in
three moments. Run from the repository: python tools/preview.py (its windows flash by at the bottom left of the
screen: drawn off screen, Qt wouldn't have the system's fonts)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DESKPET_DATA"] = tempfile.mkdtemp()

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from deskpet import desktop, face, paths  # noqa: E402
from deskpet.chat import Chat  # noqa: E402
from deskpet.pet import Pet  # noqa: E402
from deskpet.settings import Settings  # noqa: E402
from deskpet.strings import set_language, tr  # noqa: E402

TEXT = {
    "en": {"captions": ["Peeks up from the bottom of the screen", "Click it and ask Claude Code",
                        "Asks before it does anything big"],
           "reply": "Done: the title is navy now, 24 pt, centered on the artboard.",
           "what": "Delete the 12 unused swatches"},
    "zh": {"captions": ["从屏幕底边探出头", "点一下，问 Claude Code", "要动大东西前先问你"],
           "reply": "改好了：标题换成深蓝色，24 号，在画板上居中。",
           "what": "删除 12 个没用的色板"},
}
SCALE = 2
FRAME_W, FRAME_H, GAP, CAPTION_H = 330, 190, 16, 34


def main():
    qapp = QApplication(sys.argv)
    for font in (paths.ASSETS / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    desktop.give_focus = desktop.restore_focus = lambda *_: None
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
        canvas = QPixmap((3 * FRAME_W + 4 * GAP) * SCALE, (FRAME_H + 2 * GAP + CAPTION_H) * SCALE)
        canvas.setDevicePixelRatio(SCALE)
        canvas.fill(QColor("#1e2129"))
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        caption_font = QFont()
        caption_font.setFamilies(["Segoe UI", "Helvetica Neue", "Microsoft YaHei UI", "PingFang SC"])
        caption_font.setPixelSize(14)
        for i in range(3):
            if i == 1:
                pet.sink = pet.OUT
                chat._say(text["reply"], "reply")
            elif i == 2:
                chat._say(tr("permission", tool="Illustrator", what=text["what"]), "question", tr("in_box"))
                chat.bubble.open_input(tr("answer"), take_focus=False)
                chat._permission = print  # (waiting: ? over its head)
                chat.waiting_since = time.monotonic() - 2  # (between head tilts)
            pet._look(time.monotonic())  # (the ? pops up from now)
            QTest.qWait(400)  # (the bubble pops up)
            x0 = GAP + i * (FRAME_W + GAP)
            frame = QRectF(x0, GAP, FRAME_W, FRAME_H)
            g = QLinearGradient(frame.topLeft(), frame.bottomRight())
            g.setColorAt(0, QColor("#3a4a6b"))
            g.setColorAt(1, QColor("#7a5c7e"))
            p.save()
            p.setClipRect(frame)
            p.fillRect(frame, g)
            # the pet's window, with the screen's bottom edge at EDGE in it, 50 px in from the frame's left
            origin = QPointF(x0 + 50, frame.bottom() - pet.EDGE)
            if i:
                bubble = chat.bubble
                offset = bubble.pos() - pet.pos()
                bubble.render(p, QPoint(int(origin.x() + offset.x()), int(origin.y() + offset.y())))
            p.save()
            p.translate(origin)
            p.setClipRect(QRectF(0, 0, pet.SIZE, pet.EDGE))
            look = pet._look(time.monotonic())
            look.eyes = "happy" if i == 1 else look.eyes
            face.draw(p, look)
            p.restore()
            p.restore()
            p.setFont(caption_font)
            p.setPen(QColor(215, 215, 222))
            p.drawText(QRectF(x0, GAP + FRAME_H + 8, FRAME_W, CAPTION_H),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text["captions"][i])
        p.end()
        out = ROOT / "docs" / ("preview.png" if lang == "en" else "preview.zh-CN.png")
        canvas.save(str(out))
        print("saved", out)
        chat.bubble.close()
        pet.close()
    qapp.quit()


if __name__ == "__main__":
    main()
