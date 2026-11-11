"""Cached, display-independent glass materials and peripheral illustrations.

The images deliberately contain no labels.  Text and interactive feedback stay
in the UI, while the expensive light fields are rendered only when a material's
geometry changes.  Returned images are shared cache entries; callers should
copy an image before modifying it.
"""

from collections import OrderedDict
import math

from PIL import Image, ImageColor, ImageDraw, ImageFilter


_LANCZOS = Image.Resampling.LANCZOS
_SUPERSAMPLE = 4


def _dimensions(width, height=None):
    return max(1, int(round(width))), max(1, int(round(width if height is None else height)))


def _remember(cache, key, value, limit, max_bytes=None):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)
    if max_bytes is not None:
        used = sum(im.width * im.height * len(im.getbands()) for im in cache.values())
        while cache and used > max_bytes:
            _, image = cache.popitem(last=False)
            used -= image.width * image.height * len(image.getbands())
    return value


def _cached(cache, key):
    value = cache.get(key)
    if value is not None:
        cache.move_to_end(key)
    return value


def _rgba(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return ImageColor.getcolor(value, "RGBA")
    color = tuple(value)
    return color + (255,) if len(color) == 3 else color


def _curve(start, segments, scale=1.0, steps=24):
    """Flatten cubic curves for Pillow without another rendering dependency."""
    points = [(start[0] * scale, start[1] * scale)]
    x0, y0 = start
    for x1, y1, x2, y2, x3, y3 in segments:
        for index in range(1, steps + 1):
            t = index / steps
            u = 1.0 - t
            points.append(((u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3) * scale,
                           (u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3) * scale))
        x0, y0 = x3, y3
    return points


class GlassRenderer:
    """Create RGB backgrounds and RGBA panels/art with bounded LRU caches."""

    def __init__(self):
        self._background_size = None
        self._background_image = None
        self._panel_source = None
        self._panels = OrderedDict()
        self._devices = OrderedDict()
        self._shapes = OrderedDict()
        self._icons = OrderedDict()

    def shape(self, w, h, radius=18, fill=None, outline=None, width=1,
              kind="rounded", start=0, extent=360, inset=0):
        """Return a 4x-antialiased shape with transparent corners.

        Output dimensions are rounded to integers (at least one pixel).  Radius,
        stroke width and inset retain subpixel precision.  An arc uses Tk's
        angle convention: zero points right and positive angles run counter-
        clockwise.  Its stroke uses ``outline``, falling back to ``fill``.
        The shape cache is limited to 192 entries and 16 MiB of pixel data.
        """
        w, h = _dimensions(w, h)
        radius, width = max(0., float(radius)), max(0., float(width))
        inset = max(0., float(inset))
        start, extent = float(start), float(extent)
        fill, outline = _rgba(fill), _rgba(outline)
        kind = str(kind).lower()
        key = (w, h, radius, fill, outline, width, kind, start, extent, inset)
        cached = _cached(self._shapes, key)
        if cached is not None:
            return cached
        s = _SUPERSAMPLE
        high = Image.new("RGBA", (w * s, h * s))
        bounds = (inset * s, inset * s, (w - inset) * s - 1, (h - inset) * s - 1)
        if bounds[2] >= bounds[0] and bounds[3] >= bounds[1]:
            draw = ImageDraw.Draw(high)
            stroke = max(1, round(width * s)) if width > 0 else 0
            if kind == "arc":
                color = outline if outline is not None else fill
                if color is not None and stroke and extent:
                    if abs(extent) >= 360:
                        draw.ellipse(bounds, outline=color, width=stroke)
                    elif extent > 0:
                        draw.arc(bounds, -start - extent, -start, fill=color, width=stroke)
                    else:
                        draw.arc(bounds, -start, -start - extent, fill=color, width=stroke)
            elif kind == "ellipse":
                draw.ellipse(bounds, fill=fill, outline=outline if stroke else None, width=stroke)
            else:
                draw.rounded_rectangle(bounds, radius=radius * s, fill=fill,
                                       outline=outline if stroke else None, width=stroke)
        result = high.resize((w, h), _LANCZOS)
        return _remember(self._shapes, key, result, 192, 16 * 1024 * 1024)

    def icon(self, kind, size=20, color="#087F79"):
        """Return a 4x-antialiased line icon with rounded stroke caps and joins.

        Supported names are ``devices``, ``settings``, ``wave`` and ``refresh``.
        Icons are cached up to 96 entries / 2 MiB and contain no background.
        """
        size, _ = _dimensions(size)
        kind, color = str(kind).lower(), _rgba(color)
        if kind not in ("devices", "settings", "wave", "refresh"):
            raise ValueError("Unknown icon kind: " + kind)
        key = (kind, size, color)
        cached = _cached(self._icons, key)
        if cached is not None:
            return cached
        dim = size * _SUPERSAMPLE
        scale = dim / 24.
        result = Image.new("RGBA", (dim, dim))
        draw = ImageDraw.Draw(result)

        def box(values):
            return tuple(v * scale for v in values)

        def rounded_line(points, weight=1.65):
            scaled = [(x * scale, y * scale) for x, y in points]
            stroke = max(1, round(weight * scale))
            draw.line(scaled, fill=color, width=stroke, joint="curve")
            half = stroke / 2.
            for x, y in scaled:
                draw.ellipse((x - half, y - half, x + half, y + half), fill=color)

        if kind == "devices":
            for x, y in ((4.2, 4.2), (13.3, 4.2), (4.2, 13.3), (13.3, 13.3)):
                draw.rounded_rectangle(box((x, y, x + 6.5, y + 6.5)), radius=1.7 * scale,
                                       outline=color, width=max(1, round(1.65 * scale)))
        elif kind == "settings":
            for y, knob_x in ((6, 8), (12, 16), (18, 10)):
                radius = 2.3
                rounded_line([(4, y), (knob_x - radius, y)], 1.6)
                rounded_line([(knob_x + radius, y), (20, y)], 1.6)
                draw.ellipse(box((knob_x - radius, y - radius, knob_x + radius, y + radius)),
                             outline=color, width=max(1, round(1.6 * scale)))
        elif kind == "wave":
            for x, height in zip((4, 8, 12, 16, 20), (5, 11, 18, 12, 6)):
                rounded_line([(x, 12 - height / 2), (x, 12 + height / 2)], 2.0)
        else:
            points = [(12 + 7.3 * math.cos(math.radians(a)),
                       12 + 7.3 * math.sin(math.radians(a))) for a in range(40, 337, 4)]
            rounded_line(points, 1.7)
            # One clear arrowhead avoids a doubled visual weight at small sizes.
            end_x, end_y = points[-1]
            angle = math.radians(336)
            tx, ty = -math.sin(angle), math.cos(angle)
            rounded_line([(end_x - 4.2 * tx - 2.4 * ty, end_y - 4.2 * ty + 2.4 * tx),
                          (end_x, end_y),
                          (end_x - 4.2 * tx + 2.4 * ty, end_y - 4.2 * ty - 2.4 * tx)], 1.7)
        result = result.resize((size, size), _LANCZOS)
        return _remember(self._icons, key, result, 96, 2 * 1024 * 1024)

    def background(self, w, h):
        """Return a silver-white light field with softly refracting ice ribbons."""
        w, h = _dimensions(w, h)
        if self._background_size == (w, h):
            return self._background_image
        self._panels.clear()
        self._panel_source = None
        self._background_size = (w, h)

        # A small light field keeps resize work independent of monitor density.
        fw = min(240, w)
        fh = min(240, max(1, round(fw * h / w)))
        field = Image.new("RGB", (fw, fh))
        pixels = []
        for yy in range(fh):
            ny = yy / max(1, fh - 1)
            for xx in range(fw):
                nx = xx / max(1, fw - 1)
                ice = math.exp(-(((nx - .92) / .43) ** 2 + ((ny - .64) / .49) ** 2))
                mint = math.exp(-(((nx - .42) / .40) ** 2 + ((ny - 1.05) / .32) ** 2))
                pearl = math.exp(-(((nx - .39) / .52) ** 2 + ((ny - .16) / .42) ** 2))
                pixels.append((int(240 + pearl * 11 - ice * 24 - mint * 8),
                               int(244 + pearl * 8 - ice * 10 - mint * 1),
                               int(247 + pearl * 5 - ice * 4 - mint * 4)))
        field.putdata(pixels)
        base = field.resize((w, h), Image.Resampling.BICUBIC).convert("RGBA")

        # Broad optical shadows need little detail.  Keep their expensive blur
        # small, while rendering the separate crisp rims at 2x FINAL resolution
        # even on a 4K display (never upscale a low-resolution contour).
        soft_scale = min(1., 480 / max(w, h))
        rw, rh = max(1, round(w * soft_scale)), max(1, round(h * soft_scale))
        shade = Image.new("RGBA", (rw, rh))
        draw = ImageDraw.Draw(shade)
        box = (rw * .60, rh * .12, rw * 1.23, rh * 1.20)
        draw.ellipse(box, fill=(112, 163, 182, 30))
        inner = (rw * .66, rh * .16, rw * 1.17, rh * 1.13)
        draw.ellipse(inner, fill=(255, 255, 255, 0))
        shade = shade.filter(ImageFilter.GaussianBlur(max(.5, rw * .016)))
        sweep = _curve((-.13 * rw, .94 * rh), [
            (.15 * rw, .61 * rh, .35 * rw, 1.20 * rh, .75 * rw, .93 * rh),
            (.97 * rw, .78 * rh, 1.12 * rw, .54 * rh, 1.18 * rw, .30 * rh),
        ])
        light = Image.new("RGBA", (rw, rh))
        ld = ImageDraw.Draw(light)
        ld.line([(x, y + rh * .012) for x, y in sweep], fill=(161, 199, 205, 35),
                width=max(1, round(rw * .013)), joint="curve")
        light = light.filter(ImageFilter.GaussianBlur(max(.5, rw * .005)))
        shade.alpha_composite(light)
        base.alpha_composite(shade.resize((w, h), Image.Resampling.BICUBIC))

        rw, rh = w * 2, h * 2
        rims = Image.new("RGBA", (rw, rh))
        draw = ImageDraw.Draw(rims)
        draw.arc((rw * .60, rh * .12, rw * 1.23, rh * 1.20), 105, 298,
                 fill=(255, 255, 255, 145), width=max(4, round(rw * .009)))
        draw.arc((rw * .66, rh * .16, rw * 1.17, rh * 1.13), 112, 289,
                 fill=(255, 255, 255, 104), width=max(2, round(rw * .002)))
        draw.arc((rw * .605, rh * .125, rw * 1.225, rh * 1.195), 116, 265,
                 fill=(161, 196, 209, 68), width=max(2, round(rw * .0015)))
        sweep = _curve((-.13 * rw, .94 * rh), [
            (.15 * rw, .61 * rh, .35 * rw, 1.20 * rh, .75 * rw, .93 * rh),
            (.97 * rw, .78 * rh, 1.12 * rw, .54 * rh, 1.18 * rw, .30 * rh),
        ], steps=max(48, round(max(w, h) / 18)))
        draw.line(sweep, fill=(255, 255, 255, 150), width=max(4, round(rw * .003)), joint="curve")
        base.alpha_composite(rims.resize((w, h), _LANCZOS))
        self._background_image = base.convert("RGB")
        return self._background_image

    def panel(self, background, x, y, w, h, radius=24, tint="white"):
        """Return a rounded glass panel sampling the supplied whole-window image.

        Coordinates are in the background's coordinate system.  A softly
        magnified, blurred background gives real position-dependent refraction.
        The inset shadow and alpha corners fit within the requested dimensions.
        """
        w, h = _dimensions(w, h)
        x, y = int(round(x)), int(round(y))
        radius = max(0, min(int(round(radius)), min(w, h) // 2))
        if self._panel_source is not background:
            self._panels.clear()
            self._panel_source = background
        key = (x, y, w, h, radius, str(tint))
        cached = _cached(self._panels, key)
        if cached is not None:
            return cached

        source = background.convert("RGB") if background.mode != "RGB" else background
        # EXTENT's fractional crop bends the scene very slightly into the panel.
        inset_x, inset_y = min(5, w * .014), min(4, h * .014)
        crop = source.transform((w, h), Image.Transform.EXTENT,
                                (x + inset_x, y + inset_y, x + w - inset_x, y + h - inset_y),
                                resample=Image.Resampling.BICUBIC)
        crop = crop.filter(ImageFilter.GaussianBlur(9)).convert("RGBA")
        color = (232, 252, 247, 120) if tint == "mint" else (255, 255, 255, 132)
        if tint == "blue":
            color = (236, 246, 255, 128)
        crop.alpha_composite(Image.new("RGBA", (w, h), color))

        if min(w, h) < 8:
            crop.putalpha(self.shape(w, h, radius=radius, fill="white").getchannel("A"))
            return _remember(self._panels, key, crop, 72, 64 * 1024 * 1024)

        # A low-resolution luminance layer gives the material a polished crown.
        sheen = Image.new("RGBA", (1, max(2, min(h, 100))))
        sheen.putdata([(255, 255, 255, round(38 * (1 - i / (sheen.height - 1)) ** 3))
                       for i in range(sheen.height)])
        crop.alpha_composite(sheen.resize((w, h), Image.Resampling.BILINEAR))
        s = _SUPERSAMPLE
        mw, mh = w * s, h * s
        mask = Image.new("L", (mw, mh))
        md = ImageDraw.Draw(mask)
        pad = min(3, max(0, min(w, h) // 10))
        bounds = (pad * s, pad * s, (w - pad) * s - 1, (h - pad) * s - 1)
        md.rounded_rectangle(bounds, radius=radius * s, fill=255)
        mask = mask.resize((w, h), _LANCZOS)
        crop.putalpha(mask)

        result = Image.new("RGBA", (w, h))
        shadow = Image.new("RGBA", (w, h), (70, 105, 119, 0))
        shadow_mask = Image.new("L", (w, h))
        sd = ImageDraw.Draw(shadow_mask)
        sd.rounded_rectangle((pad, pad + 3, max(pad, w - pad - 1), max(pad + 3, h - 2)),
                             radius=radius, fill=29)
        shadow.putalpha(shadow_mask.filter(ImageFilter.GaussianBlur(3)))
        result.alpha_composite(shadow)
        result.alpha_composite(crop)

        edge = Image.new("RGBA", (mw, mh))
        ed = ImageDraw.Draw(edge)
        ed.rounded_rectangle(bounds, radius=radius * s, outline=(255, 255, 255, 240), width=s)
        offset = 1.5 * s
        inner = (bounds[0] + offset, bounds[1] + offset, bounds[2] - offset, bounds[3] - offset)
        if inner[2] > inner[0] and inner[3] > inner[1]:
            ed.rounded_rectangle(inner, radius=max(0, radius * s - offset),
                                 outline=(149, 178, 190, 31), width=max(1, round(s / 2)))
        # Offset arcs create a bright outside lip and a darker refracted rim.
        r = radius * s
        if r > 1.5 * s and w > radius * 2 + 10 and h > radius * 2 + 10:
            left, top, right, bottom = bounds
            ed.arc((left, top, left + r * 2, top + r * 2), 182, 268,
                   fill=(255, 255, 255, 255), width=round(1.5 * s))
            ed.line((left + r, top, right - r, top), fill=(255, 255, 255, 230), width=round(1.5 * s))
            ed.arc((right - r * 2, bottom - r * 2, right, bottom), 4, 86,
                   fill=(114, 163, 174, 67), width=s)
            ed.line((left + r, bottom - s / 2, right - r, bottom - s / 2),
                    fill=(127, 169, 180, 42), width=max(1, round(s / 2)))
        result.alpha_composite(edge.resize((w, h), _LANCZOS))
        return _remember(self._panels, key, result, 72, 64 * 1024 * 1024)

    def device(self, kind, size=180, accent="#168C80"):
        """Return a silver peripheral illustration on a transparent square."""
        size, _ = _dimensions(size)
        kind = str(kind).lower()
        try:
            color = ImageColor.getrgb(accent)[:3]
        except (ValueError, TypeError):
            color = (22, 140, 128)
        key = (kind, size, color)
        cached = _cached(self._devices, key)
        if cached is not None:
            return cached
        # The artwork uses 200 logical units, rendered at 4x final size.
        scale = size * _SUPERSAMPLE / 200
        dim = size * _SUPERSAMPLE
        art = Image.new("RGBA", (dim, dim))

        def box(values):
            return tuple(value * scale for value in values)

        def line(draw, points, fill, width=1):
            draw.line([(px * scale, py * scale) for px, py in points], fill=fill,
                      width=max(1, round(width * scale)), joint="curve")

        def metal(mask, brightness=0, cool=True):
            fw = min(96, dim)
            field = Image.new("RGBA", (fw, fw))
            pixels = []
            for yy in range(fw):
                ny = yy / max(1, fw - 1)
                for xx in range(fw):
                    nx = xx / max(1, fw - 1)
                    highlight = math.exp(-(((nx - .38) / .23) ** 2 + ((ny - .29) / .50) ** 2))
                    shade = 219 + highlight * 33 - max(0, nx - .48) * 91 - max(0, ny - .68) * 25 + brightness
                    shade = max(30, min(251, shade))
                    pixels.append((int(shade - (4 if cool else 0)), int(shade),
                                   int(min(255, shade + (5 if cool else 0))), 255))
            field.putdata(pixels)
            field = field.resize((dim, dim), Image.Resampling.BICUBIC)
            field.putalpha(mask)
            art.alpha_composite(field)

        shadow = Image.new("RGBA", (dim, dim))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.ellipse(box((43, 158, 164, 181)), fill=(79, 112, 124, 52))
        art.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8 * scale)))
        if kind == "mouse":
            points = _curve((100, 28), [
                (130, 28, 141, 46, 145, 80),
                (150, 114, 151, 145, 133, 163),
                (117, 179, 78, 177, 62, 161),
                (47, 146, 51, 112, 54, 80),
                (57, 46, 70, 28, 100, 28),
            ], scale)
            body = Image.new("L", (dim, dim))
            ImageDraw.Draw(body).polygon(points, fill=255)
            metal(body)
            draw = ImageDraw.Draw(art)
            draw.line(points + [points[0]], fill=(133, 151, 161, 155), width=max(1, round(scale)))
            outer = _curve((96, 31), [(65, 31, 60, 53, 57, 83), (51, 120, 53, 146, 63, 157)], scale)
            draw.line(outer, fill=(255, 255, 255, 237), width=max(1, round(2.1 * scale)), joint="curve")
            seam = _curve((57, 87), [(79, 91, 120, 91, 143, 85)], scale)
            draw.line(seam, fill=(130, 149, 158, 134), width=max(1, round(.85 * scale)))
            line(draw, [(100, 31), (100, 84)], (130, 149, 158, 160), .8)
            draw.rounded_rectangle(box((94, 46, 106, 74)), radius=6 * scale, fill=(102, 118, 126, 255))
            draw.rounded_rectangle(box((96, 47, 104, 73)), radius=4 * scale, fill=(172, 186, 193, 255))
            for yy in range(51, 70, 4):
                line(draw, [(97, yy), (103, yy)], (221, 230, 235, 190), .7)
            draw.ellipse(box((97.5, 101, 102.5, 106)), fill=(*color, 210))
            # The fine lower inset and a restrained light slit finish the shell.
            edge = _curve((65, 156), [(80, 170, 118, 170, 133, 155)], scale)
            draw.line(edge, fill=(95, 124, 135, 85), width=max(1, round(scale)))
            edge = _curve((80, 162), [(92, 166, 109, 166, 120, 162)], scale)
            draw.line(edge, fill=(*color, 135), width=max(1, round(1.4 * scale)))
            art = art.rotate(-17, resample=Image.Resampling.BICUBIC)
        elif kind in ("headset", "headphone", "headphones"):
            band = Image.new("L", (dim, dim))
            bd = ImageDraw.Draw(band)
            bd.arc(box((41, 28, 159, 165)), 176, 364, fill=255, width=max(1, round(17 * scale)))
            metal(band, brightness=-5)
            draw = ImageDraw.Draw(art)
            draw.arc(box((44, 31, 156, 162)), 180, 360, fill=(255, 255, 255, 230), width=max(1, round(2 * scale)))
            draw.arc(box((55, 42, 145, 158)), 192, 348, fill=(122, 139, 149, 255), width=max(1, round(5 * scale)))
            for left in (35, 136):
                draw.rounded_rectangle(box((left + 5, 95, left + 22, 145)), radius=8 * scale,
                                       fill=(140, 156, 165, 255))
                pad = Image.new("L", (dim, dim))
                ImageDraw.Draw(pad).rounded_rectangle(box((left, 103, left + 29, 161)), radius=13 * scale, fill=255)
                metal(pad, brightness=3)
                draw = ImageDraw.Draw(art)
                draw.rounded_rectangle(box((left, 103, left + 29, 161)), radius=13 * scale,
                                       outline=(255, 255, 255, 235), width=max(1, round(scale)))
                inner = left + 22 if left == 35 else left - 1
                draw.rounded_rectangle(box((inner, 110, inner + 8, 156)), radius=4 * scale,
                                       fill=(100, 119, 129, 255))
                line(draw, [(left + 7, 114), (left + 7, 143)], (255, 255, 255, 185), 1.3)
            draw.ellipse(box((145, 146, 149, 150)), fill=(*color, 230))
            mic = _curve((46, 149), [(43, 171, 63, 179, 85, 170)], scale)
            draw.line(mic, fill=(114, 135, 145, 255), width=max(1, round(3 * scale)), joint="curve")
            draw.rounded_rectangle(box((78, 164, 94, 172)), radius=4 * scale, fill=(149, 167, 177, 255))
            line(draw, [(81, 166), (89, 166)], (230, 242, 247, 255), .9)
        elif kind == "keyboard":
            # Slightly oblique projection keeps every individual key readable.
            points = [(29, 77), (166, 66), (185, 132), (43, 149)]
            lower = [(29, 82), (166, 71), (185, 140), (43, 157)]
            draw = ImageDraw.Draw(art)
            draw.polygon([(a * scale, b * scale) for a, b in lower], fill=(151, 170, 180, 255))
            body = Image.new("L", (dim, dim))
            ImageDraw.Draw(body).polygon([(a * scale, b * scale) for a, b in points], fill=255)
            metal(body, brightness=4)
            draw = ImageDraw.Draw(art)
            line(draw, points + [points[0]], (255, 255, 255, 242), 1.2)
            for row in range(5):
                for col in range(12):
                    if row == 4 and 3 < col < 8:
                        continue
                    xx = 35 + col * 10.6 + row * 2.5
                    yy = 80 + row * 11.5 - col * .87
                    key_width = 51 if row == 4 and col == 3 else 8.3
                    pts = [(xx, yy), (xx + key_width, yy - key_width * .082),
                           (xx + key_width + 2, yy + 8 - key_width * .082), (xx + 2, yy + 8)]
                    draw.polygon([(a * scale, b * scale + scale) for a, b in pts], fill=(144, 164, 175, 255))
                    draw.polygon([(a * scale, b * scale) for a, b in pts],
                                 fill=(*color, 175) if row == 0 and col == 0 else (246, 250, 252, 255))
                    line(draw, [pts[0], pts[1]], (255, 255, 255, 245), .7)
            line(draw, [(49, 151), (174, 137)], (222, 238, 244, 230), 1)
            line(draw, [(147, 143), (165, 141)], (*color, 210), 1.3)
        elif kind in ("gamepad", "controller"):
            points = _curve((65, 66), [
                (77, 62, 85, 70, 100, 70), (115, 70, 124, 61, 137, 66),
                (157, 73, 181, 140, 164, 153), (146, 165, 135, 129, 123, 126),
                (112, 122, 87, 122, 77, 126), (65, 130, 52, 165, 36, 153),
                (19, 139, 45, 72, 65, 66),
            ], scale)
            mask = Image.new("L", (dim, dim))
            ImageDraw.Draw(mask).polygon(points, fill=255)
            metal(mask)
            draw = ImageDraw.Draw(art)
            draw.line(points, fill=(255, 255, 255, 240), width=max(1, round(scale)))
            draw.rounded_rectangle(box((51, 87, 72, 94)), radius=2 * scale, fill=(117, 137, 148, 255))
            draw.rounded_rectangle(box((58, 80, 65, 102)), radius=2 * scale, fill=(117, 137, 148, 255))
            for xx, yy in ((139, 80), (148, 89), (139, 98), (130, 89)):
                draw.ellipse(box((xx - 3.7, yy - 3.7, xx + 3.7, yy + 3.7)), fill=(*color, 210))
            for xx in (79, 119):
                draw.ellipse(box((xx - 11, 103, xx + 11, 125)), fill=(142, 161, 171, 255))
                draw.ellipse(box((xx - 8, 104, xx + 8, 120)), fill=(189, 204, 212, 255))
            draw.ellipse(box((97, 88, 103, 94)), fill=(*color, 170))
        else:
            mask = Image.new("L", (dim, dim))
            ImageDraw.Draw(mask).rounded_rectangle(box((57, 60, 143, 155)), radius=22 * scale, fill=255)
            metal(mask)
            draw = ImageDraw.Draw(art)
            draw.rounded_rectangle(box((57, 60, 143, 155)), radius=22 * scale,
                                   outline=(255, 255, 255, 235), width=max(1, round(scale)))
            draw.rounded_rectangle(box((79, 37, 121, 65)), radius=5 * scale, fill=(177, 195, 205, 255))
            draw.rounded_rectangle(box((82, 39, 118, 61)), radius=3 * scale, fill=(225, 236, 242, 255))
            for xx in (91, 105):
                draw.rectangle(box((xx, 45, xx + 5, 52)), fill=(139, 161, 173, 255))
            draw.ellipse(box((96, 126, 104, 134)), fill=(*color, 205))
            for rr in (13, 23):
                draw.arc(box((100 - rr, 106 - rr, 100 + rr, 106 + rr)), 214, 326,
                         fill=(129, 154, 168, 190), width=max(1, round(2 * scale)))
        result = art.resize((size, size), _LANCZOS)
        return _remember(self._devices, key, result, 32, 16 * 1024 * 1024)
