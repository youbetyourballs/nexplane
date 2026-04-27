interface LogoMarkProps {
  className?: string;
}

export function LogoMark({ className = "" }: LogoMarkProps) {
  return (
    <svg
      viewBox="0 0 100 100"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      className={className}
    >
      <defs>
        <linearGradient id="lm-teal" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4bf" />
          <stop offset="100%" stopColor="#0d9488" />
        </linearGradient>
        <linearGradient id="lm-blue" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#60a5fa" />
          <stop offset="100%" stopColor="#1d4ed8" />
        </linearGradient>
        <linearGradient id="lm-navy" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1e3a5f" />
          <stop offset="100%" stopColor="#0b1d34" />
        </linearGradient>
      </defs>

      {/* Teal layer — bottom */}
      <rect
        x="14" y="63" width="72" height="24" rx="11"
        fill="url(#lm-teal)"
        transform="rotate(-22 50 75)"
      />

      {/* Blue layer — middle */}
      <rect
        x="14" y="38" width="72" height="24" rx="11"
        fill="url(#lm-blue)"
        transform="rotate(-22 50 50)"
      />

      {/* Navy layer — top */}
      <rect
        x="14" y="13" width="72" height="24" rx="11"
        fill="url(#lm-navy)"
        transform="rotate(-22 50 25)"
      />

      {/* Connected-path icon */}
      <path
        d="M72,15 L46,50 L22,84"
        stroke="white"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="72" cy="15" r="5" fill="white" />
      <circle cx="46" cy="50" r="5" fill="white" />
      <circle cx="22" cy="84" r="5" fill="white" />
    </svg>
  );
}
