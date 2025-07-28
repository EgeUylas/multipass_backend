'use client';

import React, { useState, useRef, useEffect } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { MessageSquare, Terminal, Settings } from 'lucide-react';
import ChatMessage from '@/components/ChatMessage';
import ChatInput from '@/components/ChatInput';
import VMStatus from '@/components/VMStatus';
import { sendChatMessage } from '@/lib/api';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  vmOperation?: {
    type: 'creating' | 'success' | 'error';
    vmName?: string;
  };
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isInitialized, setIsInitialized] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [showSidebar, setShowSidebar] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    // Component mount olduğunda benzersiz bir session ID oluştur
    setSessionId(uuidv4());
  }, []);

  useEffect(() => {
    // Component mount olduktan sonra welcome mesajını ekle (hydration mismatch'i önlemek için)
    if (!isInitialized) {
      setMessages([{
        id: 'welcome',
        role: 'assistant',
        content: 'Merhaba! Ben Multipass VM Yönetim Asistanınızım. Size sanal makinelerinizi yönetmekte yardımcı olabilirim.\n\nYapabileceklerim:\n• VM oluşturma (örn: "Ubuntu VM oluştur")\n• VM listeleme (örn: "VM\'leri listele")\n• VM yönetimi (başlatma, durdurma, silme)\n\nNasıl yardımcı olabilirim?',
        timestamp: new Date(),
      }]);
      setIsInitialized(true);
    }
  }, [isInitialized]);

  const handleSendMessage = async (content: string) => {
    if (isLoading) return;

    // Kullanıcı mesajını ekle
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content,
      timestamp: new Date(),
    };

    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      // VM oluşturma işlemi kontrolü
      const isVMCreation = content.toLowerCase().includes('oluştur') && 
                          (content.toLowerCase().includes('vm') || content.toLowerCase().includes('sanal'));
      
      // Geçici asistan mesajı ekle
      const tempId = `temp-${Date.now()}`;
      const tempMessage: Message = {
        id: tempId,
        role: 'assistant',
        content: 'İşleniyor...',
        timestamp: new Date(),
        isStreaming: true,
      };

      // VM oluşturma işlemiyse VM adını çıkarmaya çalış
      if (isVMCreation) {
        const vmNameMatch = content.match(/(\w+)\s*vm/i) || content.match(/vm\s*(\w+)/i);
        const vmName = vmNameMatch ? vmNameMatch[1] : 'yeni-vm';
        
        tempMessage.vmOperation = {
          type: 'creating',
          vmName: vmName
        };
      }

      setMessages(prev => [...prev, tempMessage]);

      // Backend API'sine istek gönder
      const response = await sendChatMessage(content, sessionId);
      
      // VM listesini otomatik yenile (eğer başarılı komut varsa)
      const hasSuccessfulCommand = response?.execution_results?.some(result => result.success);
      if (hasSuccessfulCommand) {
        setTimeout(() => {
          // Event dispatch ederek VMStatus'u yenile
          window.dispatchEvent(new CustomEvent('refreshVMs'));
        }, 2000);
      }
      
      // Yanıtı güncelle
      setMessages(prev => prev.map(msg => 
        msg.id === tempId 
          ? { 
              ...msg, 
              content: response?.response || 'İşlem tamamlandı.', 
              isStreaming: false,
              vmOperation: response?.vmOperation || msg.vmOperation
            } 
          : msg
      ));

    } catch (error) {
      console.error('Hata:', error);
      
      let errorContent = 'Üzgünüm, bir hata oluştu. ';
      if (error instanceof Error) {
        if (error.message.includes('fetch') || error.message.includes('Network')) {
          errorContent += 'Backend sunucusuna bağlanılamıyor. Lütfen backend servislerinin çalıştığından emin olun.';
        } else {
          errorContent += `Hata: ${error.message}`;
        }
      } else {
        errorContent += 'Bilinmeyen bir hata oluştu.';
      }
      
      setMessages(prev => [
        ...prev, 
        {
          id: `error-${Date.now()}`,
          role: 'assistant',
          content: errorContent,
          timestamp: new Date(),
          vmOperation: { type: 'error' }
        }
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      {showSidebar && (
        <div className="w-80 bg-white border-r border-gray-200 flex flex-col">
          <div className="p-4 border-b border-gray-200">
            <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <Terminal className="w-6 h-6 text-blue-600" />
              Multipass Manager
            </h2>
            <p className="text-sm text-gray-600 mt-1">VM Yönetim Asistanı</p>
          </div>
          
          <div className="flex-1 overflow-y-auto">
            <VMStatus />
          </div>
          
          <div className="p-4 border-t border-gray-200">
            <button
              onClick={() => setShowSidebar(false)}
              className="flex items-center gap-2 text-sm text-gray-600 hover:text-gray-900"
            >
              <Settings className="w-4 h-4" />
              Yan paneli gizle
            </button>
          </div>
        </div>
      )}

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {!showSidebar && (
                <button
                  onClick={() => setShowSidebar(true)}
                  className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg"
                >
                  <Terminal className="w-5 h-5" />
                </button>
              )}
              <div>
                <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
                  <MessageSquare className="w-6 h-6 text-blue-600" />
                  Multipass Chat
                </h1>
                <p className="text-sm text-gray-600">
                  {isLoading ? 'Asistan yazıyor...' : 'Komutlarınızı bekliyor'}
                </p>
              </div>
            </div>
            
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              Bağlı
            </div>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          {messages.map((message) => (
            <ChatMessage key={message.id} message={message} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <ChatInput
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
        />
      </div>
    </div>
  );
}
