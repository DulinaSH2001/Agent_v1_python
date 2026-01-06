/** @type {import('next').NextConfig} */
const nextConfig = {
    reactStrictMode: true,
    experimental: {
        // Next.js 16 features
        serverActions: {
            bodySizeLimit: '2mb',
        },
    },
    images: {
        domains: ['images.unsplash.com', 'avatars.githubusercontent.com'],
    },
};

module.exports = nextConfig;
