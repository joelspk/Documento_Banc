# Combinador de PDFs - pronto para publicar

## Rodar no computador

1. Instale Python 3.11 ou superior.
2. Descompacte este pacote.
3. Abra o terminal dentro da pasta.
4. Execute:

```bash
pip install -r requirements.txt
python app.py
```

5. Abra no navegador:

```text
http://127.0.0.1:8123
```

## Publicar no Render

1. Envie estes arquivos para um repositório no GitHub.
2. No Render, crie um novo Web Service usando esse repositório.
3. Use o `render.yaml` incluído no pacote.
4. Configure a variável `APP_PASSWORD` para proteger o acesso.

## Arquivos grandes, acima de 500 MB

Esta versão está configurada com `MAX_UPLOAD_MB=2048`, ou seja, até aproximadamente 2 GB por lote, desde que a hospedagem tenha memória, tempo de processamento e espaço em disco suficientes.

Para Render, recomenda-se usar plano pago (`starter` ou superior) e disco persistente montado em `/var/data`, porque o plano gratuito perde arquivos ao reiniciar/spin down e não é adequado para lotes muito grandes.

Variáveis importantes:

```text
MAX_UPLOAD_MB=2048
JOBS_DIR=/var/data/web_jobs
JOB_TTL_HOURS=24
APP_PASSWORD=sua-senha
```

## Correção de erro JSON

A interface agora não quebra mais com `Unexpected end of JSON input`. Se o servidor devolver HTML, erro 413, erro 502 ou resposta vazia, a tela mostra a resposta real recebida para facilitar o diagnóstico.

## Observação operacional

O sistema guarda os arquivos processados temporariamente para permitir o download do PDF final e do relatório. A limpeza automática remove trabalhos antigos conforme `JOB_TTL_HOURS`.
