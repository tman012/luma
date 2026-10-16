/**
 * UserMenu — Dropdown menu for user icon.
 *
 * When logged out: Login / Sign Up
 * When logged in: Profile / Settings / Logout
 */

import { useRef, useState, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';

export function UserMenu() {
  const navigate = useNavigate();
  const { authenticated, logout, user } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Close menu when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        menuRef.current &&
        buttonRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        !buttonRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => {
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [isOpen]);

  // Close menu when navigating
  const handleNavigate = (path: string) => {
    setIsOpen(false);
    navigate(path);
  };

  // Handle logout
  const handleLogout = async () => {
    setIsOpen(false);
    await logout();
    navigate('/');
  };

  return (
    <div style={{ position: 'relative' }}>
      {/* User icon button */}
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-label="User menu"
        aria-expanded={isOpen}
        style={{
          flexShrink: 0,
          width: 42,
          height: 42,
          borderRadius: '50%',
          background: isOpen ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.1)',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'background 0.15s',
        }}
      >
        <svg width="22" height="22" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <circle cx="9" cy="7" r="3" stroke="rgba(255,255,255,0.85)" strokeWidth="1.4" />
          <path
            d="M2.5 16c0-3.314 2.91-6 6.5-6s6.5 2.686 6.5 6"
            stroke="rgba(255,255,255,0.85)"
            strokeWidth="1.4"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {/* Dropdown menu */}
      {isOpen && (
        <div
          ref={menuRef}
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: 8,
            minWidth: 180,
            background: 'rgba(15,15,15,0.96)',
            backdropFilter: 'blur(24px)',
            WebkitBackdropFilter: 'blur(24px)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 12,
            boxShadow: '0 12px 48px rgba(0,0,0,0.5)',
            zIndex: 1000,
            overflow: 'hidden',
          }}
        >
          {authenticated ? (
            <>
              {/* Authenticated menu items */}
              <MenuItem label="Profile" onClick={() => handleNavigate('/profile')} />
              {user?.is_admin ? (
                <MenuItem label="Settings" onClick={() => handleNavigate('/settings')} />
              ) : (
                <MenuItem label="My Settings" onClick={() => handleNavigate('/my-settings')} />
              )}
              <div
                style={{
                  height: 1,
                  background: 'rgba(255,255,255,0.08)',
                  margin: '4px 0',
                }}
              />
              <MenuItem label="Logout" onClick={handleLogout} danger />
            </>
          ) : (
            <>
              {/* Unauthenticated menu items */}
              <MenuItem label="Login" onClick={() => handleNavigate('/login')} />
              <MenuItem label="Sign Up" onClick={() => handleNavigate('/login')} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Menu item component
function MenuItem({
  label,
  onClick,
  danger = false,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'block',
        width: '100%',
        padding: '10px 16px',
        background: isHovered ? 'rgba(255,255,255,0.1)' : 'transparent',
        border: 'none',
        color: danger ? '#ef4444' : 'rgba(255,255,255,0.85)',
        fontSize: 14,
        fontWeight: 500,
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'background 0.15s',
        fontFamily: 'inherit',
      }}
    >
      {label}
    </button>
  );
}
