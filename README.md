# Resume PDF Renderer Demo

这是一个最小可运行的第二段服务：接收工作流传来的 HTML，渲染 PDF，并把 PDF 暴露为可下载链接。

## 启动

```bash
docker build -t resume-pdf-renderer .
docker run --rm -p 8000:8000 -e PUBLIC_BASE_URL=http://127.0.0.1:8000 resume-pdf-renderer
```

也可以直接运行：

```bash
python -m pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 健康检查

```bash
curl http://127.0.0.1:8000/health
```

预期返回：

```json
{"status":"ok"}
```

## 渲染测试

```bash
curl -X POST http://127.0.0.1:8000/render \
  -H "Content-Type: application/json" \
  -d '{
    "file_name": "张三-Java后端实习生.pdf",
    "html": "<html><body><h1>张三</h1><p>Java 后端实习生</p></body></html>"
  }'
```

响应示例：

```json
{
  "success": true,
  "file_name": "张三-Java后端实习生.pdf",
  "file_url": "http://127.0.0.1:8000/files/xxx.pdf",
  "message": "PDF 已生成"
}
```

服务同时支持表单提交：

```bash
curl -X POST http://127.0.0.1:8000/render \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "file_name=张三-Java后端实习生.pdf" \
  --data-urlencode "html=<h1>张三</h1>"
```

## 部署说明

- 星辰工作流必须能访问这个服务的 `/render` 地址。
- 如果星辰在云端，`127.0.0.1` 不可用，需要部署到星辰可访问的云服务器、内网或使用临时公网隧道。
- 正式环境建议把 `OUTPUT_DIR` 换成对象存储，并在 `PUBLIC_BASE_URL` 中填写文件服务的公网域名。
