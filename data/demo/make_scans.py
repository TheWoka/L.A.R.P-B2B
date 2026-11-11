"""T-077a: только синтетические сканы, Pillow + PyMuPDF; без OCR и вызовов LLM.

python data/demo/make_scans.py [--font /path/to/DejaVuSans.ttf] [--out data/demo]
python data/demo/make_scans.py --self-test
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from random import Random

import pymupdf
from PIL import Image, ImageDraw, ImageFont

# Поддерживает и checkout, и bind mount /data_rw/demo в api-контейнере.
sys.path.insert(0, str(Path("/app") if Path("/app/app").is_dir()
                       else Path(__file__).resolve().parents[2] / "backend"))
from app.services.detection import validators  # noqa: E402

LOG = logging.getLogger(__name__)
NOTICE = "УЧЕБНЫЙ ДОКУМЕНТ. Все сведения вымышлены"
DEFAULT_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DPI = 200
A4_PIXELS = (round(210 / 25.4 * DPI), round(297 / 25.4 * DPI))
A4_POINTS = (210 / 25.4 * 72, 297 / 25.4 * 72)
Page = tuple[str, ...]


def scenarios(rng: Random) -> tuple[Page, Page, Page]:
    """Ключ ответов независим от детектора; случайность задаёт только реквизиты."""
    first9 = str(rng.randrange(1_001_999, 1_000_000_000)).zfill(9)
    checksum = validators.snils_checksum(first9)
    snils = f"{first9[:3]}-{first9[3:6]}-{first9[6:]} {checksum:02d}"
    passport = f"{rng.randint(1, 99):02d} 11 {rng.randint(0, 999999):06d}"
    # Придуманный ИНН организации с корректной контрольной цифрой, не ИНН физлица.
    inn = next(f"990000011{digit}" for digit in range(10)
               if validators.is_valid_inn(f"990000011{digit}"))
    return (
        (
            "Заявка на участие в закупке", "",
            "Реквизиты участника", "Участник: ООО «Бета-Пример».",
            f"ИНН организации: {inn}.", "",
            "Просим принять заявку на поставку учебных материалов.",
            "Сведения о руководителе приведены на второй странице.", "",
            "Контактное лицо: Лебедев Артём Павлович.",
            "Личный телефон: +7 (900) 000-00-11.",
            "Электронная почта контактного лица: email@example.ru.",
        ),
        (
            "Сведения о руководителе", "",
            "Руководитель: Соколов Роман Денисович.",
            f"Паспорт: {passport}.", f"СНИЛС: {snils}.",
            "Дата рождения: 11.04.1986.", "",
            "Сведения приложены к заявке ООО «Бета-Пример».",
            "Документ подготовлен для размещения на учебной площадке.",
        ),
        (
            "Анкета работника для пропуска", "",
            "Работник: Морозов Тимур Олегович,",
            "проживающий по адресу г. Примерск, ул. Учебная, д. 11, кв. 5.",
            "Личный телефон: +7 (900) 000-00-12.",
            "Группа инвалидности: III", "",
            "Цель: оформление пропуска на учебную территорию.",
            "Подразделение: отдел закупок ООО «Бета-Пример».",
        ),
    )


def load_font(path: Path) -> ImageFont.FreeTypeFont:
    """Шрифт задаётся явно; подмена кириллицы системным шрифтом недопустима."""
    if not path.is_file():
        raise ValueError("Не найден DejaVuSans.ttf; укажите путь через --font")
    return ImageFont.truetype(str(path), size=34)


def render_page(rows: Page, font: ImageFont.FreeTypeFont) -> Image.Image:
    """Рисует чёрный текст на белом А4; проверяет поля до поворота скана."""
    if not rows or not rows[0].strip():
        raise ValueError("Страница должна содержать заголовок")
    image = Image.new("L", A4_PIXELS, 255)
    draw = ImageDraw.Draw(image)
    draw.text((120, 110), NOTICE, fill=0, font=font.font_variant(size=26))
    y = 230
    for index, text in enumerate(rows):
        face = font.font_variant(size=46) if index == 0 else font
        if draw.textlength(text, font=face) > image.width - 240:
            raise ValueError("Строка не помещается по ширине страницы")
        if y + face.size > image.height - 140:
            raise ValueError("Текст не помещается по высоте страницы")
        draw.text((120, y), text, fill=0, font=face)
        y += 110 if index == 0 else 72
    return image


def add_noise(image: Image.Image, rng: Random, sigma: float = 6) -> Image.Image:
    """Независимый гауссов шум до ограничения диапазоном 0…255, без numpy."""
    if image.mode != "L" or not 0 <= sigma < float("inf"):
        raise ValueError("Нужны изображение L и конечная неотрицательная sigma")
    pixels = bytes(max(0, min(255, round(value + rng.gauss(0, sigma))))
                   for value in image.tobytes())
    return Image.frombytes("L", image.size, pixels)


def scan_jpeg(image: Image.Image, rng: Random, angle: float) -> bytes:
    """Шум sigma=6, поворот без обрезания текста и JPEG quality=85, 200 dpi."""
    if not -2 <= angle <= 2:
        raise ValueError("Допустим только лёгкий поворот от -2 до 2 градусов")
    scanned = add_noise(image, rng).rotate(
        angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255,
    )
    output = BytesIO()
    scanned.save(output, format="JPEG", quality=85, dpi=(DPI, DPI))
    return output.getvalue()


def write_pdf(path: Path, pages: tuple[bytes, ...]) -> None:
    """В PDF вставляются только JPEG: ни шрифтов, ни скрытого текстового слоя."""
    if not pages:
        raise ValueError("PDF должен содержать хотя бы одну страницу")
    with pymupdf.open() as document:
        for stream in pages:
            page = document.new_page(width=A4_POINTS[0], height=A4_POINTS[1])
            page.insert_image(page.rect, stream=stream)
        # Не создаём случайный trailer ID или временные метки: повторяем и байты.
        path.write_bytes(document.tobytes(garbage=4, deflate=True, no_new_id=True))


def generate(out: Path, font_path: Path = DEFAULT_FONT) -> None:
    """Перезаписывает только 11/12; Random(11) управляет реквизитами и шумом."""
    if out.exists() and not out.is_dir():
        raise ValueError("--out должен указывать на каталог")
    font = load_font(font_path)
    rng = Random(11)
    pages = scenarios(rng)
    out.mkdir(parents=True, exist_ok=True)
    images = tuple(scan_jpeg(render_page(rows, font), rng, angle)
                   for rows, angle in zip(pages, (1.5, 1.5, -1.0), strict=True))
    write_pdf(out / "11_scan_application.pdf", images[:2])
    (out / "12_scan_anketa.jpg").write_bytes(images[2])
    LOG.info("T-077a: создано 2 синтетических скана, 3 страницы, 200 dpi")


class ScanTests(unittest.TestCase):
    """Тесты не распознают текст и не подгоняют ожидания под детектор."""

    font_path = DEFAULT_FONT

    def test_scenarios_and_checksum_boundaries(self) -> None:
        self.assertEqual(scenarios(Random(11)), scenarios(Random(11)))
        for seed in (0, 11, 99):
            with self.subTest(seed=seed):
                page1, page2, anketa = scenarios(Random(seed))
                snils = next(row for row in page2 if row.startswith("СНИЛС:"))[7:-1]
                self.assertTrue(validators.is_valid_snils(snils))
                digits = validators.only_digits(snils)
                assert digits is not None
                self.assertGreater(int(digits[:9]), 1_001_998)
                invalid = snils[:-1] + str((int(snils[-1]) + 1) % 10)
                self.assertFalse(validators.is_valid_snils(invalid))
                passport = next(row for row in page2 if row.startswith("Паспорт:"))[9:-1]
                self.assertTrue(validators.is_valid_passport_series(passport[:5]))
                self.assertTrue(1 <= int(passport[:2]) <= 99)
                inn = next(row for row in page1 if row.startswith("ИНН"))[-11:-1]
                self.assertEqual(len(inn), 10)
                self.assertTrue(validators.is_valid_inn(inn))
                self.assertIn("Группа инвалидности: III", anketa)

    def test_load_font_and_missing_path(self) -> None:
        self.assertIsInstance(load_font(self.font_path), ImageFont.FreeTypeFont)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "--font"):
                load_font(Path(folder) / "missing.ttf")

    def test_render_and_overflow(self) -> None:
        font = load_font(self.font_path)
        for rows in scenarios(Random(11)):
            image = render_page(rows, font)
            self.assertEqual(image.size, A4_PIXELS)
            self.assertEqual(image.getextrema(), (0, 255))
        for rows in ((), ("",), ("X" * 500,), ("Title",) + ("Line",) * 100):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                render_page(rows, font)

    def test_noise_determinism_and_distribution(self) -> None:
        image = Image.new("L", (200, 200), 128)
        noisy = add_noise(image, Random(11))
        self.assertEqual(noisy.tobytes(), add_noise(image, Random(11)).tobytes())
        values = list(noisy.tobytes())
        mean = sum(values) / len(values)
        sigma = (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5
        self.assertAlmostEqual(mean, 128, delta=0.15)
        self.assertAlmostEqual(sigma, 6, delta=0.15)
        self.assertEqual(add_noise(image, Random(0), 0).tobytes(), image.tobytes())
        for sigma in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                add_noise(image, Random(0), sigma)
        with self.assertRaises(ValueError):
            add_noise(image.convert("RGB"), Random(0))

    def test_scan_jpeg_and_angle_limit(self) -> None:
        image = Image.new("L", (80, 120), 255)
        ImageDraw.Draw(image).rectangle((20, 30, 30, 80), fill=0)
        for angle in (1.5, -1.0, -2, 2):
            data = scan_jpeg(image, Random(11), angle)
            self.assertEqual(data, scan_jpeg(image, Random(11), angle))
            with Image.open(BytesIO(data)) as result:
                self.assertEqual(result.size, image.size)
                self.assertEqual(result.info["dpi"], (DPI, DPI))
                self.assertEqual(result.format, "JPEG")
        for angle in (-3, 3, float("nan")):
            with self.assertRaises(ValueError):
                scan_jpeg(image, Random(11), angle)

    def test_pdf_images_only_and_empty(self) -> None:
        stream = scan_jpeg(Image.new("L", (80, 120), 255), Random(11), 1.5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scan.pdf"
            write_pdf(path, (stream, stream))
            first = path.read_bytes()
            write_pdf(path, (stream, stream))
            self.assertEqual(path.read_bytes(), first)
            with pymupdf.open(path) as document:
                self.assertEqual(len(document), 2)
                for page in document:
                    self.assertEqual(page.get_text(), "")
                    self.assertEqual(page.get_fonts(), [])
                    self.assertEqual(len(page.get_images()), 1)
                    self.assertAlmostEqual(page.rect.width, A4_POINTS[0], places=3)
                    self.assertAlmostEqual(page.rect.height, A4_POINTS[1], places=3)
            with self.assertRaises(ValueError):
                write_pdf(path, ())

    def test_generate_repeat_and_preserve_key(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            key = out / "answers.yaml"
            key.write_text("unchanged", encoding="utf-8")
            self.assertEqual(main(["--out", str(out), "--font", str(self.font_path)]), 0)
            first = {path.name: path.read_bytes() for path in out.iterdir()}
            generate(out, self.font_path)
            self.assertEqual({path.name: path.read_bytes() for path in out.iterdir()}, first)
            self.assertEqual(len(first), 3)
            with Image.open(out / "12_scan_anketa.jpg") as image:
                self.assertEqual(image.size, A4_PIXELS)
                self.assertEqual(image.info["dpi"], (200, 200))
            with pymupdf.open(out / "11_scan_application.pdf") as document:
                for page in document:
                    self.assertEqual(page.get_text(), "")
                    self.assertEqual(page.get_images()[0][2:4], A4_PIXELS)
            with self.assertRaises(ValueError):
                generate(key, self.font_path)
            with self.assertRaises(ValueError):
                generate(out, out / "missing.ttf")
            self.assertEqual(main(["--out", str(key), "--font", str(self.font_path)]), 1)


def main(argv: list[str] | None = None) -> int:
    """CLI: без аргументов создаёт оба файла рядом со скриптом."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        ScanTests.font_path = args.font
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ScanTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    try:
        generate(args.out, args.font)
    except (ValueError, OSError):
        LOG.error("Не удалось создать сканы: проверьте --font и доступность --out")
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
