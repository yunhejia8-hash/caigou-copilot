"""Private quotation images, decoded locally and sent to the configured vision API."""
import base64
import io
import warnings

import frappe
from suke_ops.vendor.procurement_agent import procurement as quotes


def validate_upload(name):
    if not isinstance(name, str) or len(name) > 140:
        raise ValueError('图片文件编号无效。')
    file = frappe.get_doc('File', name)
    if file.owner != frappe.session.user or not file.is_private or file.is_folder or not (file.file_url or '').startswith('/private/files/'):
        frappe.throw('只能使用自己上传的私有报价图片。', frappe.PermissionError)
    if file.file_size and file.file_size > 10 * 1024 * 1024:
        raise ValueError('报价图片不能超过10MB。')
    return file


def image_url(content):
    from PIL import Image
    if not isinstance(content, bytes) or len(content) > 10 * 1024 * 1024:
        raise ValueError('报价图片不能超过10MB。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                mime = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)
                if not mime or image.width * image.height > 25000000 or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError()
                image.verify()
    except Exception:
        raise ValueError('请上传有效的PNG、JPG或WebP静态图片（不超过2500万像素）。') from None
    return 'data:'+mime+';base64,'+base64.b64encode(content).decode('ascii')


def extract(name):
    file = validate_upload(name)
    url = image_url(file.get_content())
    raw = quotes.model_quotes([{'role':'user','content':[
        {'type':'input_text','text':'逐项提取这张供应商报价图片。模糊或缺失字段返回null，不从申请预算或其他供应商推断。最多20项报价。'},
        {'type':'input_image','image_url':url,'detail':'high'}]}])
    if not isinstance(raw, list) or len(raw) > 20:
        raise ValueError('图片报价数量或格式无效，请拆分图片重试。')
    return [dict(quotes.validate_quote(row), evidence='图片文件 '+name+'；'+str(row.get('evidence') or '')) for row in raw]
