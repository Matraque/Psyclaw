import { Conversation } from "./conversation";
import { getConnectionConfig } from "./config";

export function App() {
  const config = getConnectionConfig();

  return (
    <main className="app-shell">
      <header className="app-header">
        <a className="wordmark" href="/" aria-label="Psyclaw home">Psyclaw</a>
      </header>
      <Conversation config={config} />
    </main>
  );
}
