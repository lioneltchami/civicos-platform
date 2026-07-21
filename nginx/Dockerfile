FROM nginx:1.25-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/civicos.conf
COPY api-root.html /usr/share/nginx/html/api-root.html
EXPOSE 80
