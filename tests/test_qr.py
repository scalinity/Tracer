import pytest
from PIL import Image

from src.api import qr


def pixels(path):
    return Image.open(path).convert("L")


def test_the_committed_png_is_what_the_code_writes_for_the_public_address(tmp_path):
    fresh = qr.write(tmp_path / "qr.png")
    assert pixels(qr.OUT_PATH).tobytes() == pixels(fresh).tobytes() and pixels(qr.OUT_PATH).size == pixels(fresh).size


def test_the_code_is_black_on_white_with_a_quiet_zone_and_a_size_that_scans_from_afar():
    img = pixels(qr.OUT_PATH)
    assert set(img.tobytes()) == {0, 255} and img.size[0] == img.size[1] and 400 <= img.size[0] <= 1000
    border = qr.BORDER_MODULES * qr.BOX_PIXELS
    assert all(img.getpixel((x, y)) == 255 for x in range(img.size[0]) for y in (0, border - 1)) and img.getpixel((img.size[0] - 1, img.size[1] - 1)) == 255


def test_the_address_is_the_pages_address_of_the_demo():
    assert qr.PUBLIC_URL == "https://scalinity.github.io/Tracer/demo/" and qr.PUBLIC_URL.endswith("/demo/")
    assert qr.OUT_PATH.parent.name == "demo" and qr.OUT_PATH.name == "qr.png"


def test_a_different_address_gives_a_different_code(tmp_path):
    other = qr.write(tmp_path / "other.png", "https://example.org/")
    assert pixels(other).tobytes() != pixels(qr.OUT_PATH).tobytes()


def test_the_code_decodes_to_the_address():
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(qr.OUT_PATH))
    text, points, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert text == qr.PUBLIC_URL and points is not None


def test_the_code_still_decodes_when_it_is_shrunk_and_blurred_like_a_phone_photo():
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(qr.OUT_PATH))
    small = cv2.GaussianBlur(cv2.resize(img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA), (5, 5), 0)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(small)
    assert text == qr.PUBLIC_URL
