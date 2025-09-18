FROM nginx:alpine
RUN apk add --no-cache openssl bash
WORKDIR /etc/nginx
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /etc/nginx/certs
EXPOSE 443
ENTRYPOINT ["/entrypoint.sh"]
