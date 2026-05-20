import struct, zlib, math, os

def write_png(filename, size, pixels):
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t+d) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(c for px in pixels[y*size:(y+1)*size] for c in px) for y in range(size))
    data = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    open(filename, 'wb').write(data)

def lerp(a, b, t):
    return int(a + (b - a) * t)

def make_icon(size):
    BG   = (15, 23, 42, 255)    # #0f172a
    BLUE = (59, 130, 246, 255)  # #3b82f6
    TRAN = (0, 0, 0, 0)

    pixels = []
    cr = size * 0.19            # corner radius
    pad = size * 0.14           # padding for M letter
    th  = max(int(size * 0.10), 3)  # stroke thickness

    # M letter definition (normalized 0-1 coordinates)
    # Two vertical bars + two diagonals meeting at center-top
    m_segs = [
        # left bar: x0,y0 -> x1,y1 (rectangle)
        (pad, pad, pad+th, size-pad),
        # right bar
        (size-pad-th, pad, size-pad, size-pad),
    ]
    # Diagonals as filled polys — approximate with pixels
    m_left_diag_x  = [pad, pad+th,   size//2+th//2, size//2-th//2]
    m_left_diag_y  = [pad, pad,       size//2,       size//2]
    m_right_diag_x = [size-pad, size-pad-th, size//2-th//2, size//2+th//2]
    m_right_diag_y = [pad, pad,              size//2,        size//2]

    def in_rect(x, y, x0, y0, x1, y1):
        return x0 <= x < x1 and y0 <= y < y1

    def sign(p1, p2, p3):
        return (p1[0]-p3[0])*(p2[1]-p3[1]) - (p2[0]-p3[0])*(p1[1]-p3[1])

    def in_triangle(px, py, t):
        d1 = sign((px,py), t[0], t[1])
        d2 = sign((px,py), t[1], t[2])
        d3 = sign((px,py), t[2], t[0])
        has_neg = (d1<0) or (d2<0) or (d3<0)
        has_pos = (d1>0) or (d2>0) or (d3>0)
        return not (has_neg and has_pos)

    def in_quad(px, py, pts):
        return (in_triangle(px, py, [pts[0],pts[1],pts[2]]) or
                in_triangle(px, py, [pts[0],pts[2],pts[3]]))

    ld = list(zip(m_left_diag_x, m_left_diag_y))
    rd = list(zip(m_right_diag_x, m_right_diag_y))

    for y in range(size):
        for x in range(size):
            # Round corners: transparent outside
            in_tl = x < cr and y < cr and math.hypot(x-cr, y-cr) > cr
            in_tr = x > size-1-cr and y < cr and math.hypot(x-(size-1-cr), y-cr) > cr
            in_bl = x < cr and y > size-1-cr and math.hypot(x-cr, y-(size-1-cr)) > cr
            in_br = x > size-1-cr and y > size-1-cr and math.hypot(x-(size-1-cr), y-(size-1-cr)) > cr

            if in_tl or in_tr or in_bl or in_br:
                pixels.append(TRAN)
                continue

            # Check if inside M letter segments
            in_m = False
            # Left bar
            if in_rect(x, y, pad, pad, pad+th, size-pad):
                in_m = True
            # Right bar
            elif in_rect(x, y, size-pad-th, pad, size-pad, size-pad):
                in_m = True
            # Left diagonal quad
            elif in_quad(x, y, ld):
                in_m = True
            # Right diagonal quad
            elif in_quad(x, y, rd):
                in_m = True

            pixels.append(BLUE if in_m else BG)

    return pixels

for sz in [192, 512]:
    px = make_icon(sz)
    write_png(f'/opt/mdm/static/icons/icon-{sz}.png', sz, px)
    print(f'icon-{sz}.png OK')

print('Done')
