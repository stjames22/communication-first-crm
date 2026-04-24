FROM node:22-slim

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src ./src
COPY public ./public
COPY db ./db
COPY scripts ./scripts

RUN npm run build
RUN npm prune --omit=dev

EXPOSE 3000

CMD ["sh", "-c", "node scripts/migrateDb.js && npm start"]
