# 服务器部署

需要服务器已安装 Docker（含 compose 插件）。产物目录与临时目录都会自动创建，
不需要预先 mkdir。三种方式按服务器条件选一种即可。

## 方式一：服务器上直接构建（推荐，只需上传源码几十 KB）

```bash
cd /srv/resume-pdf-renderer
cp deploy/.env.server.example deploy/.env.server
vi deploy/.env.server          # 改密钥、改 PUBLIC_BASE_URL
cd deploy && docker compose up -d --build
docker compose logs -f         # 出现 /readyz 正常即就绪
```

服务器构建时需要能访问 apt 源与 pypi（要装 Chromium 与中文字体）。国内服务器可先在
`/etc/docker/daemon.json` 配置镜像加速。

## 方式二：本地构建后上传镜像 tar（服务器不能联网时用）

```bash
# 本地构建并导出
docker build -t resume-pdf-renderer:1.0.0 .
docker save resume-pdf-renderer:1.0.0 -o resume-pdf-renderer-1.0.0.tar

# 上传到服务器（tar 约 600MB，含 Chromium 与中文字体）
scp resume-pdf-renderer-1.0.0.tar deploy/.env.server user@server:/srv/rpr/

# 服务器上导入并启动
cd /srv/rpr && docker load -i resume-pdf-renderer-1.0.0.tar
docker run -d --name rpr --restart unless-stopped -p 8000:8000 \
  --env-file .env.server \
  -v /srv/rpr/artifacts:/data/artifacts \
  --shm-size=256m --pids-limit=512 \
  resume-pdf-renderer:1.0.0
```

`docker run` 方式改配置要 `docker rm -f rpr` 再重新 run；用 compose 则改完
`.env.server` 执行 `docker compose up -d` 即可。

## 方式三：不用 Docker（Python 直装）

```bash
cd /srv/resume-pdf-renderer
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium    # 需要 root 装系统库
set -a; . ./deploy/.env.server; set +a
.venv/bin/uvicorn resume_pdf.main:create_app --factory --app-dir src --host 0.0.0.0 --port 8000
```

长期运行用 systemd 托管，注意把 `WorkingDirectory` 设为项目根目录。

## 上线后自检

```bash
curl -s http://127.0.0.1:8000/readyz
curl -s http://127.0.0.1:8000/v1/openapi.agent.yaml | head -20   # 确认 servers[0].url 是对外地址
```

然后在星辰平台上「从 URL 中导入」`http://<对外地址>/v1/openapi.agent.yaml`。

## 用域名访问（Nginx 反代）

注意：在面板里「添加反代」只写了 Nginx 配置，**不会自动创建 DNS 记录**，两件事要分别做。

1. 在域名服务商为子域加 A 记录，指向服务器公网 IP（`pdf` → `47.108.20.34`）；
   如果面板是 CDN/WAF 类型（面板里给了「接入地址/CNAME 目标」），则应加 CNAME 指向它，
   回源地址保持 `http://47.108.20.34:8000`。
2. 安全组放行 **80**（启用 HTTPS 再放行 443）。只放行 8000 时，域名访问进不来。
3. 用 `deploy/nginx.conf.example` 作为 server 块：注意 `client_max_body_size` 必须不小于
   `RPR_MAX_REQUEST_BYTES`（默认 4m），否则大 HTML 会被网关拦成 413。
4. 把 `RPR_PUBLIC_BASE_URL` 改成 `https://pdf.linqin.space`（或 `http://pdf.linqin.space`）并重启，
   让返回的 `file_url` 与 Schema 里的 `servers[0].url` 都用域名。
5. 自检：`curl -I http://pdf.linqin.space/healthz`、`curl -s http://pdf.linqin.space/v1/openapi.agent.yaml | head -20`。
