# 构建阶段复用固定版本运行环境；最终镜像从空层复制，不能从旧层取回完整 ERPNext 源码。
FROM frappe/erpnext:v16.53.0 AS prepared
USER root
RUN rm -rf /home/frappe/frappe-bench/apps/erpnext /home/frappe/frappe-bench/assets/erpnext
COPY --chown=frappe:frappe apps/erpnext /home/frappe/frappe-bench/apps/erpnext
COPY --chown=frappe:frappe apps/suke_ops /home/frappe/frappe-bench/apps/suke_ops
COPY --chown=frappe:frappe deploy/initialize.py /home/frappe/initialize_focus.py
COPY deploy/prepare_runtime.py /tmp/prepare_focus_runtime.py
RUN python /tmp/prepare_focus_runtime.py
USER frappe
ENV PYTHONPATH=/home/frappe/frappe-bench/apps/suke_ops
WORKDIR /home/frappe/frappe-bench
RUN printf 'frappe\nerpnext\nsuke_ops\n' > sites/apps.txt \
    && cd apps/erpnext && yarn install --frozen-lockfile --ignore-scripts \
    && cd ../.. && bench build --app frappe,erpnext,suke_ops \
    && cp -a sites/assets/. assets/ \
    && test -f assets/suke_ops/hub.js \
    && test ! -d apps/erpnext/erpnext/crm \
    && test ! -d apps/erpnext/erpnext/assets \
    && test ! -d apps/erpnext/erpnext/projects

FROM scratch AS runtime
COPY --from=prepared / /
ENV PATH=/home/frappe/.nvm/current/bin/:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    NVM_DIR=/home/frappe/.nvm \
    NVM_SYMLINK_CURRENT=true \
    PYTHONPATH=/home/frappe/frappe-bench/apps/suke_ops
USER frappe
WORKDIR /home/frappe/frappe-bench
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["start.sh"]
