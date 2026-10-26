import Chat from "@/components/Chat";
import { ConfirmProvider } from "@/components/ConfirmDialog";

export const metadata = {
  title: "Chat - Multimodal RAG",
};

export default function ChatPage() {
  return (
    <ConfirmProvider>
      <Chat />
    </ConfirmProvider>
  );
}
