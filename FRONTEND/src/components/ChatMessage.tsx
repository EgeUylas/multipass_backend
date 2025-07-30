import React, { useState, useEffect } from 'react';
import { User, Bot, Terminal, CheckCircle, XCircle, Clock } from 'lucide-react';

interface ChatMessageProps {
  message: {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    isStreaming?: boolean;
    vmOperation?: {
      type: 'creating' | 'success' | 'error';
      vmName?: string;
    };
  };
}

export default function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const [formattedTime, setFormattedTime] = useState('');
  
  useEffect(() => {
    // Client-side'da timestamp formatla (hydration mismatch önlemek için)
    setFormattedTime(message.timestamp.toLocaleTimeString('tr-TR'));
  }, [message.timestamp]);
  
  const getVMOperationIcon = () => {
    if (!message.vmOperation) return null;
    
    switch (message.vmOperation.type) {
      case 'creating':
        return <Clock className="w-4 h-4 text-yellow-500 animate-spin" />;
      case 'success':
        return <CheckCircle className="w-4 h-4 text-green-500" />;
      case 'error':
        return <XCircle className="w-4 h-4 text-red-500" />;
      default:
        return null;
    }
  };

  return (
    <div className={`flex gap-4 p-4 ${isUser ? 'bg-gray-50' : 'bg-white'} border-b border-gray-100`}>
      {/* Avatar */}
      <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
        isUser ? 'bg-blue-500 text-white' : 'bg-gray-600 text-white'
      }`}>
        {isUser ? <User className="w-5 h-5" /> : <Bot className="w-5 h-5" />}
      </div>
      
      {/* Message Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-medium text-gray-900">
            {isUser ? 'Sen' : 'Multipass Assistant'}
          </span>
          <span className="text-xs text-gray-500">
            {formattedTime}
          </span>
          {message.vmOperation && getVMOperationIcon()}
        </div>
        
        {/* Message Text */}
        <div className="text-gray-800 whitespace-pre-wrap">
          {message.content}
          {message.isStreaming && (
            <span className="inline-block w-2 h-5 bg-gray-400 animate-pulse ml-1" />
          )}
        </div>
        
        {/* VM Operation Status */}
        {message.vmOperation && (
          <div className={`mt-2 p-2 rounded-lg text-sm flex items-center gap-2 ${
            message.vmOperation.type === 'creating' ? 'bg-yellow-50 text-yellow-800' :
            message.vmOperation.type === 'success' ? 'bg-green-50 text-green-800' :
            'bg-red-50 text-red-800'
          }`}>
            <Terminal className="w-4 h-4" />
            {message.vmOperation.type === 'creating' && `VM "${message.vmOperation.vmName}" oluşturuluyor...`}
            {message.vmOperation.type === 'success' && `VM "${message.vmOperation.vmName}" başarıyla oluşturuldu!`}
            {message.vmOperation.type === 'error' && `VM oluşturma işlemi başarısız oldu.`}
          </div>
        )}
      </div>
    </div>
  );
} 