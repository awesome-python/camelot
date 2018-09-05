from __future__ import division
import os
import logging
from itertools import groupby
from operator import itemgetter

import numpy as np

from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfpage import PDFTextExtractionNotAllowed
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfdevice import PDFDevice
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import (LAParams, LTAnno, LTChar, LTTextLineHorizontal,
                             LTTextLineVertical)


def translate(x1, x2):
    x2 += x1
    return x2


def scale(x, s):
    x *= s
    return x


def rotate(x1, y1, x2, y2, angle):
    s = np.sin(angle)
    c = np.cos(angle)
    x2 = translate(-x1, x2)
    y2 = translate(-y1, y2)
    xnew = c * x2 - s * y2
    ynew = s * x2 + c * y2
    xnew = translate(x1, xnew)
    ynew = translate(y1, ynew)
    return xnew, ynew


def scale_to_image(k, factors):
    x1, y1, x2, y2 = k
    scaling_factor_x, scaling_factor_y, pdf_y = factors
    x1 = scale(x1, scaling_factor_x)
    y1 = scale(abs(translate(-pdf_y, y1)), scaling_factor_y)
    x2 = scale(x2, scaling_factor_x)
    y2 = scale(abs(translate(-pdf_y, y2)), scaling_factor_y)
    knew = (int(x1), int(y1), int(x2), int(y2))
    return knew


def scale_to_pdf(tables, v_segments, h_segments, factors):
    scaling_factor_x, scaling_factor_y, img_y = factors
    tables_new = {}
    for k in tables.keys():
        x1, y1, x2, y2 = k
        x1 = scale(x1, scaling_factor_x)
        y1 = scale(abs(translate(-img_y, y1)), scaling_factor_y)
        x2 = scale(x2, scaling_factor_x)
        y2 = scale(abs(translate(-img_y, y2)), scaling_factor_y)
        j_x, j_y = zip(*tables[k])
        j_x = [scale(j, scaling_factor_x) for j in j_x]
        j_y = [scale(abs(translate(-img_y, j)), scaling_factor_y) for j in j_y]
        joints = zip(j_x, j_y)
        tables_new[(x1, y1, x2, y2)] = joints

    v_segments_new = []
    for v in v_segments:
        x1, x2 = scale(v[0], scaling_factor_x), scale(v[2], scaling_factor_x)
        y1, y2 = scale(abs(translate(-img_y, v[1])), scaling_factor_y), scale(
            abs(translate(-img_y, v[3])), scaling_factor_y)
        v_segments_new.append((x1, y1, x2, y2))

    h_segments_new = []
    for h in h_segments:
        x1, x2 = scale(h[0], scaling_factor_x), scale(h[2], scaling_factor_x)
        y1, y2 = scale(abs(translate(-img_y, h[1])), scaling_factor_y), scale(
            abs(translate(-img_y, h[3])), scaling_factor_y)
        h_segments_new.append((x1, y1, x2, y2))

    return tables_new, v_segments_new, h_segments_new


def setup_logging(log_filepath):
    logger = logging.getLogger("app_logger")
    logger.setLevel(logging.DEBUG)
    # Log File Handler (Associating one log file per webservice run)
    log_file_handler = logging.FileHandler(log_filepath,
                                           mode='a',
                                           encoding='utf-8')
    log_file_handler.setLevel(logging.DEBUG)
    format_string = '%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'
    formatter = logging.Formatter(format_string, datefmt='%Y-%m-%dT%H:%M:%S')
    log_file_handler.setFormatter(formatter)
    logger.addHandler(log_file_handler)
    # Stream Log Handler (For console)
    stream_log_handler = logging.StreamHandler()
    stream_log_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(format_string, datefmt='%Y-%m-%dT%H:%M:%S')
    stream_log_handler.setFormatter(formatter)
    logger.addHandler(stream_log_handler)
    return logger


def get_rotation(lttextlh, lttextlv, ltchar):
    rotation = ''
    hlen = len([t for t in lttextlh if t.get_text().strip()])
    vlen = len([t for t in lttextlv if t.get_text().strip()])
    if hlen < vlen:
        clockwise = sum(t.matrix[1] < 0 and t.matrix[2] > 0 for t in ltchar)
        anticlockwise = sum(t.matrix[1] > 0 and t.matrix[2] < 0 for t in ltchar)
        rotation = 'left' if clockwise < anticlockwise else 'right'
    return rotation


def segments_bbox(bbox, v_segments, h_segments):
    lb = (bbox[0], bbox[1])
    rt = (bbox[2], bbox[3])
    v_s = [v for v in v_segments if v[1] > lb[1] - 2 and
           v[3] < rt[1] + 2 and lb[0] - 2 <= v[0] <= rt[0] + 2]
    h_s = [h for h in h_segments if h[0] > lb[0] - 2 and
           h[2] < rt[0] + 2 and lb[1] - 2 <= h[1] <= rt[1] + 2]
    return v_s, h_s


def text_in_bbox(bbox, text):
    lb = (bbox[0], bbox[1])
    rt = (bbox[2], bbox[3])
    t_bbox = [t for t in text if lb[0] - 2 <= (t.x0 + t.x1) / 2.0
                 <= rt[0] + 2 and lb[1] - 2 <= (t.y0 + t.y1) / 2.0
                 <= rt[1] + 2]
    return t_bbox


def remove_close_values(ar, mtol=2):
    ret = []
    for a in ar:
        if not ret:
            ret.append(a)
        else:
            temp = ret[-1]
            if np.isclose(temp, a, atol=mtol):
                pass
            else:
                ret.append(a)
    return ret


def merge_close_values(ar, mtol=2):
    ret = []
    for a in ar:
        if not ret:
            ret.append(a)
        else:
            temp = ret[-1]
            if np.isclose(temp, a, atol=mtol):
                temp = (temp + a) / 2.0
                ret[-1] = temp
            else:
                ret.append(a)
    return ret


def flag_on_size(textline, direction):
    if direction == 'horizontal':
        d = [(t.get_text(), np.round(t.height, decimals=6)) for t in textline if not isinstance(t, LTAnno)]
    elif direction == 'vertical':
        d = [(t.get_text(), np.round(t.width, decimals=6)) for t in textline if not isinstance(t, LTAnno)]
    l = [np.round(size, decimals=6) for text, size in d]
    if len(set(l)) > 1:
        flist = []
        min_size = min(l)
        for key, chars in groupby(d, itemgetter(1)):
            if key == min_size:
                fchars = [t[0] for t in chars]
                if ''.join(fchars).strip():
                    fchars.insert(0, '<s>')
                    fchars.append('</s>')
                    flist.append(''.join(fchars))
            else:
                fchars = [t[0] for t in chars]
                if ''.join(fchars).strip():
                    flist.append(''.join(fchars))
        fstring = ''.join(flist).strip('\n')
    else:
        fstring = ''.join([t.get_text() for t in textline]).strip('\n')
    return fstring


def split_textline(table, textline, direction, flag_size=True):
    idx = 0
    cut_text = []
    bbox = textline.bbox
    try:
        if direction == 'horizontal' and not textline.is_empty():
            x_overlap = [i for i, x in enumerate(table.cols) if x[0] <= bbox[2] and bbox[0] <= x[1]]
            r_idx = [j for j, r in enumerate(table.rows) if r[1] <= (bbox[1] + bbox[3]) / 2 <= r[0]]
            r = r_idx[0]
            x_cuts = [(c, table.cells[r][c].x2) for c in x_overlap if table.cells[r][c].right]
            if not x_cuts:
                x_cuts = [(x_overlap[0], table.cells[r][-1].x2)]
            for obj in textline._objs:
                row = table.rows[r]
                for cut in x_cuts:
                    if isinstance(obj, LTChar):
                        if (row[1] <= (obj.y0 + obj.y1) / 2 <= row[0] and
                                (obj.x0 + obj.x1) / 2 <= cut[1]):
                            cut_text.append((r, cut[0], obj))
                            break
                    elif isinstance(obj, LTAnno):
                        cut_text.append((r, cut[0], obj))
        elif direction == 'vertical' and not textline.is_empty():
            y_overlap = [j for j, y in enumerate(table.rows) if y[1] <= bbox[3] and bbox[1] <= y[0]]
            c_idx = [i for i, c in enumerate(table.cols) if c[0] <= (bbox[0] + bbox[2]) / 2 <= c[1]]
            c = c_idx[0]
            y_cuts = [(r, table.cells[r][c].y1) for r in y_overlap if table.cells[r][c].bottom]
            if not y_cuts:
                y_cuts = [(y_overlap[0], table.cells[-1][c].y1)]
            for obj in textline._objs:
                col = table.cols[c]
                for cut in y_cuts:
                    if isinstance(obj, LTChar):
                        if (col[0] <= (obj.x0 + obj.x1) / 2 <= col[1] and
                                (obj.y0 + obj.y1) / 2 >= cut[1]):
                            cut_text.append((cut[0], c, obj))
                            break
                    elif isinstance(obj, LTAnno):
                        cut_text.append((cut[0], c, obj))
    except IndexError:
        return [(-1, -1, textline.get_text())]
    grouped_chars = []
    for key, chars in groupby(cut_text, itemgetter(0, 1)):
        if flag_size:
            grouped_chars.append((key[0], key[1], flag_on_size([t[2] for t in chars], direction)))
        else:
            gchars = [t[2].get_text() for t in chars]
            grouped_chars.append((key[0], key[1], ''.join(gchars).strip('\n')))
    return grouped_chars


def get_table_index(table, t, direction, split_text=False, flag_size=True):
    r_idx, c_idx = [-1] * 2
    for r in range(len(table.rows)):
        if ((t.y0 + t.y1) / 2.0 < table.rows[r][0] and
                (t.y0 + t.y1) / 2.0 > table.rows[r][1]):
            lt_col_overlap = []
            for c in table.cols:
                if c[0] <= t.x1 and c[1] >= t.x0:
                    left = t.x0 if c[0] <= t.x0 else c[0]
                    right = t.x1 if c[1] >= t.x1 else c[1]
                    lt_col_overlap.append(abs(left - right) / abs(c[0] - c[1]))
                else:
                    lt_col_overlap.append(-1)
            if len(filter(lambda x: x != -1, lt_col_overlap)) == 0:
                logging.warning("Text did not fit any column.")
            r_idx = r
            c_idx = lt_col_overlap.index(max(lt_col_overlap))
            break

    # error calculation
    y0_offset, y1_offset, x0_offset, x1_offset = [0] * 4
    if t.y0 > table.rows[r_idx][0]:
        y0_offset = abs(t.y0 - table.rows[r_idx][0])
    if t.y1 < table.rows[r_idx][1]:
        y1_offset = abs(t.y1 - table.rows[r_idx][1])
    if t.x0 < table.cols[c_idx][0]:
        x0_offset = abs(t.x0 - table.cols[c_idx][0])
    if t.x1 > table.cols[c_idx][1]:
        x1_offset = abs(t.x1 - table.cols[c_idx][1])
    X = 1.0 if abs(t.x0 - t.x1) == 0.0 else abs(t.x0 - t.x1)
    Y = 1.0 if abs(t.y0 - t.y1) == 0.0 else abs(t.y0 - t.y1)
    charea = X * Y
    error = ((X * (y0_offset + y1_offset)) + (Y * (x0_offset + x1_offset))) / charea

    if split_text:
        return split_textline(table, t, direction, flag_size=flag_size), error
    else:
        if flag_size:
            return [(r_idx, c_idx, flag_on_size(t._objs, direction))], error
        else:
            return [(r_idx, c_idx, t.get_text().strip('\n'))], error


def compute_accuracy(error_weights):
    SCORE_VAL = 100
    try:
        score = 0
        if sum([ew[0] for ew in error_weights]) != SCORE_VAL:
            raise ValueError("Sum of weights should be equal to 100.")
        for ew in error_weights:
            weight = ew[0] / len(ew[1])
            for error_percentage in ew[1]:
                score += weight * (1 - error_percentage)
    except ZeroDivisionError:
        score = 0
    return score


def remove_empty(d):
    for i, row in enumerate(d):
        if row == [''] * len(row):
            d.pop(i)
    d = zip(*d)
    d = [list(row) for row in d if any(row)]
    d = zip(*d)
    return d


def count_empty(d):
    empty_p = 0
    r_nempty_cells, c_nempty_cells = [], []
    for i in d:
        for j in i:
            if j.strip() == '':
                empty_p += 1
    empty_p = 100 * (empty_p / float(len(d) * len(d[0])))
    for row in d:
        r_nempty_c = 0
        for r in row:
            if r.strip() != '':
                r_nempty_c += 1
        r_nempty_cells.append(r_nempty_c)
    d = zip(*d)
    d = [list(col) for col in d]
    for col in d:
        c_nempty_c = 0
        for c in col:
            if c.strip() != '':
                c_nempty_c += 1
        c_nempty_cells.append(c_nempty_c)
    return empty_p, r_nempty_cells, c_nempty_cells


def encode_(ar):
    ar = [[r.encode('utf-8') for r in row] for row in ar]
    return ar


def get_text_objects(layout, ltype="char", t=None):
    if ltype == "char":
        LTObject = LTChar
    elif ltype == "lh":
        LTObject = LTTextLineHorizontal
    elif ltype == "lv":
        LTObject = LTTextLineVertical
    if t is None:
        t = []
    try:
        for obj in layout._objs:
            if isinstance(obj, LTObject):
                t.append(obj)
            else:
                t += get_text_objects(obj, ltype=ltype)
    except AttributeError:
        pass
    return t


def get_page_layout(pname, char_margin=1.0, line_margin=0.5, word_margin=0.1,
               detect_vertical=True, all_texts=True):
    with open(pname, 'r') as f:
        parser = PDFParser(f)
        document = PDFDocument(parser)
        if not document.is_extractable:
            raise PDFTextExtractionNotAllowed
        laparams = LAParams(char_margin=char_margin,
                            line_margin=line_margin,
                            word_margin=word_margin,
                            detect_vertical=detect_vertical,
                            all_texts=all_texts)
        rsrcmgr = PDFResourceManager()
        device = PDFPageAggregator(rsrcmgr, laparams=laparams)
        interpreter = PDFPageInterpreter(rsrcmgr, device)
        for page in PDFPage.create_pages(document):
            interpreter.process_page(page)
            layout = device.get_result()
            width = layout.bbox[2]
            height = layout.bbox[3]
            dim = (width, height)
        return layout, dim


def merge_tuples(tuples):
    merged = list(tuples[0])
    for s, e in tuples:
        if s <= merged[1]:
            merged[1] = max(merged[1], e)
        else:
            yield tuple(merged)
            merged[0] = s
            merged[1] = e
    yield tuple(merged)