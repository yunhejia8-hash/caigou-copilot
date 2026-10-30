"""生成本地口令文件，已有配置不覆盖；文件已被 Git 和 Docker 构建忽略。"""
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / '.env'
with path.open('x', encoding='utf-8') as stream:
    stream.write('DB_PASSWORD=' + secrets.token_urlsafe(24) + '\n')
    stream.write('ADMIN_PASSWORD=' + secrets.token_urlsafe(24) + '\n')
    stream.write('HTTP_PORT=8081\n')
print('已生成 .env；管理员登录名 Administrator，口令请在本地文件查看。')
