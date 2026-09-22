FROM langgenius/dify-api:1.17.0
USER root
RUN uv pip install --python /app/api/.venv/bin/python reportlab==4.4.10
WORKDIR /service
COPY exam_tools.py /service/exam_tools.py
ENTRYPOINT ["/app/api/.venv/bin/python", "/service/service.py"]
