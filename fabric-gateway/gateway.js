'use strict';

const express = require('express');
const cors = require('cors');
const path = require('path');
const os = require('os');
const grpc = require('@grpc/grpc-js');
const { connect, Contract, Identity, Signer, signers } = require('@hyperledger/fabric-gateway');
const crypto = require('crypto');
const { promises: fs } = require('fs');

// --- Configuration ---
const homeDirectory = os.homedir();
const channelName = 'mychannel';
const chaincodeName = 'marketplace';
const mspId = 'Org1MSP';

// Paths to the crypto materials. This assumes your 'go' workspace is in your home directory.
const cryptoPath = path.resolve(homeDirectory, 'go', 'src', 'github.com', 'hyperledger', 'fabric-samples', 'test-network', 'organizations', 'peerOrganizations', 'org1.example.com');
const keyDirectoryPath = path.resolve(cryptoPath, 'users', 'Admin@org1.example.com', 'msp', 'keystore');
const certDirectoryPath = path.resolve(cryptoPath, 'users', 'Admin@org1.example.com', 'msp', 'signcerts');
const tlsCertPath = path.resolve(cryptoPath, 'peers', 'peer0.org1.example.com', 'tls', 'ca.crt');
const peerEndpoint = 'localhost:7051';
const peerName = 'peer0.org1.example.com';

// --- Main Application ---
const app = express();
app.use(cors());
app.use(express.json());

const PORT = 8001;
let contract;

// --- Helper Functions ---
async function newGrpcConnection() {
    const tlsRootCert = await fs.readFile(tlsCertPath);
    const tlsCredentials = grpc.credentials.createSsl(tlsRootCert);
    return new grpc.Client(peerEndpoint, tlsCredentials, {
        'grpc.ssl_target_name_override': peerName,
    });
}

async function newIdentity() {
    const certFiles = await fs.readdir(certDirectoryPath);
    const certPath = path.resolve(certDirectoryPath, certFiles[0]);
    const credentials = await fs.readFile(certPath);
    return { mspId, credentials };
}

async function newSigner() {
    const files = await fs.readdir(keyDirectoryPath);
    const keyPath = path.resolve(keyDirectoryPath, files[0]);
    const privateKeyPem = await fs.readFile(keyPath);
    const privateKey = crypto.createPrivateKey(privateKeyPem);
    return signers.newPrivateKeySigner(privateKey);
}

// --- API Endpoints ---
app.post('/record-transaction-on-chain', async (req, res) => {
    console.log('\n--> Submitting Transaction: RecordSale, arguments:', req.body);
    const { product_id, price } = req.body;
    if (!product_id || !price) {
        return res.status(400).json({ error: 'product_id and price are required' });
    }
    try {
        const timestamp = new Date().toISOString();
        const resultBytes = await contract.submitTransaction('RecordSale', product_id, price, timestamp);
        const resultJson = JSON.parse(Buffer.from(resultBytes).toString());
        console.log('*** Transaction committed successfully. Result:', resultJson);
        return res.status(200).json(resultJson);
    } catch (error) {
        console.error('******** FAILED to submit transaction:', error);
        return res.status(500).json({ error: 'Failed to submit transaction', details: error.message });
    }
});

// THIS IS THE ENDPOINT THAT WAS MISSING OR INCORRECT IN YOUR OLD FILE
app.get('/query/sales/:productId', async (req, res) => {
    const productId = req.params.productId;
    console.log(`\n--> Evaluating Transaction: GetSalesByProduct, for product: ${productId}`);
    try {
        // evaluateTransaction is for read-only queries that don't need to be ordered
        const resultBytes = await contract.evaluateTransaction('GetSalesByProduct', productId);
        const resultJson = JSON.parse(Buffer.from(resultBytes).toString());
        console.log('*** Query successful. Result:', resultJson);
        return res.status(200).json(resultJson);
    } catch (error) {
        console.error('******** FAILED to query transaction:', error);
        return res.status(500).json({ error: 'Failed to query transaction', details: error.message });
    }
});

// --- Main Execution ---
async function main() {
    console.log('--- Initializing Fabric Gateway Service ---');
    try {
        const client = await newGrpcConnection();
        const gateway = connect({
            client,
            identity: await newIdentity(),
            signer: await newSigner(),
            evaluateOptions: () => ({ deadline: Date.now() + 5000 }), // 5 seconds
            endorseOptions: () => ({ deadline: Date.now() + 15000 }), // 15 seconds
            submitOptions: () => ({ deadline: Date.now() + 5000 }), // 5 seconds
            commitStatusOptions: () => ({ deadline: Date.now() + 60000 }), // 1 minute
        });

        const network = gateway.getNetwork(channelName);
        contract = network.getContract(chaincodeName);

        app.listen(PORT, () => {
            console.log(`\n*** Fabric Gateway service listening on port ${PORT} ***`);
        });
    } catch (error) {
        console.error('******** FAILED to run the application:', error);
        process.exitCode = 1;
    }
}

main();

