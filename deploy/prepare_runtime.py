"""保留跨站 Origin；同源轮询未带 Origin 时使用请求自身的主机。"""
from pathlib import Path

template = Path('/templates/nginx/frappe.conf.template')
source = template.read_text()
upstream = 'proxy_set_header Origin $proxy_x_forwarded_proto://${FRAPPE_SITE_NAME_HEADER};'
previous = 'proxy_set_header Origin $http_origin;'
replacement = 'proxy_set_header Origin $socketio_origin;'
if not any(value in source for value in (upstream, previous, replacement)):
    raise RuntimeError('上游 nginx 模板已变化，需要重新核验实时连接配置。')
source = source.replace(upstream, replacement).replace(previous, replacement)
# 浏览器同源 polling GET 可以省略 Origin。只补这个空值；外站明确发来的
# Origin 必须原样保留，由实时服务继续拒绝不一致的来源。
origin_map = '''map $http_origin $socketio_origin {
    default $http_origin;
    "" $proxy_x_forwarded_proto://$http_host;
}

'''
if 'map $http_origin $socketio_origin' not in source:
    if '\nserver {' not in source:
        raise RuntimeError('上游 nginx server 块已变化。')
    source = source.replace('\nserver {', '\n' + origin_map + 'server {', 1)
template.write_text(source)
