/** @type {import('next').NextConfig} */
const nextConfig = {
    reactStrictMode: true,
    swcMinify: false,
    // SWC issues are common in WebContainer, Next.js 15 has improved fallbacks
    // but we keep config clean for best compatibility
    images: {
        remotePatterns: [
            {
                protocol: 'https',
                hostname: 'images.unsplash.com',
            },
            {
                protocol: 'https',
                hostname: 'avatars.githubusercontent.com',
            },
        ],
    },
};

export default nextConfig;
