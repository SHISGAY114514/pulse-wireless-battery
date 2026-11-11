"""Pixel-quality and bounded-cache checks; no Tk window or hardware needed."""
import unittest

from PIL import Image

from glass_renderer import GlassRenderer


class GlassRendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = GlassRenderer()

    def assert_antialiased_alpha(self, image):
        self.assertEqual(image.mode, "RGBA")
        alpha = set(image.getchannel("A").tobytes())
        self.assertIn(0, alpha)
        self.assertGreater(max(alpha), 200)
        self.assertGreater(len(alpha - {0, 255}), 8,
                           "Curves need multiple partial-coverage pixels, not a binary mask")

    def test_curved_primitives_have_transparent_corners_and_partial_edge_coverage(self):
        for kind in ("rounded", "ellipse", "arc"):
            with self.subTest(kind=kind):
                image = self.renderer.shape(47, 31, radius=12, kind=kind,
                                            fill="white" if kind != "arc" else None,
                                            outline="white", width=1.5, start=20, extent=280)
                self.assertEqual(image.size, (47, 31))
                self.assert_antialiased_alpha(image)
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                if kind != "arc":
                    self.assertEqual(image.getpixel((23, 15))[3], 255)

    def test_icons_keep_transparent_antialiased_strokes_at_small_and_large_sizes(self):
        for kind in ("devices", "settings", "wave", "refresh"):
            for size in (16, 20, 48):
                with self.subTest(kind=kind, size=size):
                    image = self.renderer.icon(kind, size=size)
                    self.assertEqual(image.size, (size, size))
                    self.assert_antialiased_alpha(image)
                    self.assertEqual(image.getpixel((0, 0))[3], 0)
                    self.assertIs(image, self.renderer.icon(kind, size=size))

    @staticmethod
    def outer_edges(image):
        alpha = image.getchannel("A")
        edges = []
        for y in range(image.height):
            opaque = [x for x in range(image.width) if alpha.getpixel((x, y)) >= 128]
            edges.append((opaque[0], opaque[-1]) if opaque else None)
        return edges

    def test_material_and_hover_share_the_same_rounded_outer_contour(self):
        background = Image.new("RGB", (200, 100), "#DDE8EC")
        for w, h, radius in ((144, 46, 22), (65, 30, 15), (120, 40, 20)):
            with self.subTest(size=(w, h)):
                panel = self.renderer.panel(background, 5, 5, w, h, radius=radius)
                outline = self.renderer.shape(w, h, radius=radius, outline="#FFFFFF", inset=3)
                hover = self.renderer.shape(w, h, radius=radius, outline="#21998F", inset=3)
                self.assertEqual(outline.getchannel("A").tobytes(), hover.getchannel("A").tobytes(),
                                 "Hover color must not move or reshape its contour")
                self.assert_antialiased_alpha(panel)
                for material_edge, hover_edge in zip(self.outer_edges(panel), self.outer_edges(hover)):
                    self.assertEqual(material_edge is None, hover_edge is None)
                    if material_edge is not None:
                        # Material lip/shadow can shift half-coverage by one AA pixel.
                        for expected, actual in zip(material_edge, hover_edge):
                            self.assertLessEqual(abs(expected - actual), 1)

    def test_size_normalization_cache_reuse_and_eviction_bound_animation_memory(self):
        shape = self.renderer.shape(23.2, 17.2, radius=5, fill="#123456")
        self.assertEqual(shape.size, (23, 17))
        self.assertIs(shape, self.renderer.shape(23, 17, radius=5, fill="#123456"))
        self.assertEqual(self.renderer.shape(0, -4, fill="white").size, (1, 1))
        self.assertEqual(self.renderer.icon("refresh", size=0).size, (1, 1))
        self.assertIsNot(shape, self.renderer.shape(23, 17, radius=5, fill="#654321"))
        for value in range(220):
            self.renderer.shape(23, 17, radius=5, fill=(value, 80, 120))
        self.assertLessEqual(len(self.renderer._shapes), 192)
        replacement = self.renderer.shape(23, 17, radius=5, fill="#123456")
        self.assertIsNot(shape, replacement)
        self.assertEqual(shape.tobytes(), replacement.tobytes())
        for value in range(110):
            self.renderer.icon("refresh", color=(value, 80, 120))
        self.assertLessEqual(len(self.renderer._icons), 96)
        # Large textures must obey the byte budget as well as the entry count.
        for value in range(18):
            self.renderer.shape(512, 512, radius=20, fill=(value, 80, 120))
        shape_bytes = sum(im.width * im.height * 4 for im in self.renderer._shapes.values())
        self.assertLessEqual(shape_bytes, 16 * 1024 * 1024)
        for value in range(10):
            self.renderer.icon("refresh", size=256, color=(value, 80, 120))
        icon_bytes = sum(im.width * im.height * 4 for im in self.renderer._icons.values())
        self.assertLessEqual(icon_bytes, 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
