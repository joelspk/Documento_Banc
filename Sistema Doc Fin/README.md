# Combinador de PDFs - versao para arquivos grandes

Esta versao envia os PDFs em blocos de 8 MB, processa em segundo plano e mostra progresso na tela. Ela foi criada para evitar erro 502 em lotes grandes.

## Rodar no computador

```bash
pip install -r requirements.txt
python app.py
```

Abra:

```text
http://127.0.0.1:10000
```

Se quiser usar a porta 8123 localmente:

```bash
PORT=8123 python app.py
```

## Publicar no Render

1. Envie todos estes arquivos para um repositorio no GitHub.
2. No Render, crie ou atualize o Web Service usando esse repositorio.
3. Use um plano pago com memoria suficiente. Para lotes acima de 500 MB, recomenda-se pelo menos `standard`, nao `free` nem `starter`.
4. Adicione um Persistent Disk montado em `/var/data`.
5. Configure as variaveis:

```text
APP_PASSWORD=sua-senha
MAX_UPLOAD_MB=2048
JOBS_DIR=/var/data/web_jobs
JOB_TTL_HOURS=24
```

## O que mudou nesta versao

- Upload em blocos de 8 MB.
- Processamento em segundo plano.
- Tela de progresso.
- Endpoint `/api/status` para acompanhar o processamento.
- Menor risco de 502 por timeout ou estouro de memoria.

## Observacao

Mesmo com upload em blocos, lotes muito grandes exigem memoria e CPU para ler e juntar os PDFs. Se houver erro 502 no Render, veja os logs em `Logs` e a pagina `Events`; normalmente o motivo sera memoria, timeout ou falta de disco.
