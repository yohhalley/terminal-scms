using Godot;
using System;
using System.Threading.Tasks;
using MQTTnet;
using MQTTnet.Client;
using Org.Eclipse.Tahu.Protobuf;
using Google.Protobuf;
using System.Threading.Tasks.Dataflow;

public partial class SparkPlugBMQTTManager : Node
{
    [Signal] public delegate void BrokerConnectedEventHandler();
    [Signal] public delegate void BrokerConnectionFailedEventHandler();
    [Signal] public delegate void BrokerDisconnectedEventHandler();
    [Signal] public delegate void PublishAcknowledgeEventHandler();
    [Signal] public delegate void BrokerReceivedMessageEventHandler(string topic, string message);
    [Signal] public delegate void BrokerInitializedEventHandler();
    
    private IMqttClient mqttClient;
    private ulong sequenceNumber = 0;

    [Export] public string BrokerAddress = "localhost";
    [Export] public int BrokerPort = 1883;
    [Export] public string GroupId = "LOCAL-GROUP";
    [Export] public string NodeId = "GODOT-SIM";
    [Export] public string DeviceIdPrefix = "CHE_";
    [Export] public string Username = "";
    [Export] public string Password = "";

    public override void _Ready()
    {
        // You can call ConnectToBroker here if you want auto-connect
        EmitSignal(SignalName.BrokerInitialized);
        GD.Print("SparkPlugBMQTTManager is ready.");
        
    }

    public async void ConnectToBroker(string brokerAddress, int brokerPort, string groupId, string nodeId, string username = "", string password = "")
    {
        BrokerAddress = brokerAddress;
        BrokerPort = brokerPort;
        GroupId = groupId;
        NodeId = nodeId;
        Username = username;
        Password = password;

        var factory = new MqttFactory();
        mqttClient = factory.CreateMqttClient();

        var optionsBuilder = new MqttClientOptionsBuilder()
            .WithTcpServer(BrokerAddress, BrokerPort)
            .WithClientId(NodeId)
            .WithCleanSession(false);

        if (!string.IsNullOrEmpty(Username))
            optionsBuilder = optionsBuilder.WithCredentials(Username, Password);

        var options = optionsBuilder.Build();

        mqttClient.ConnectedAsync += async e =>
        {
            GD.Print("Connected to MQTT broker.");
            EmitSignal("BrokerConnected");
            await PublishNBIRTH();
        };

        mqttClient.DisconnectedAsync += async e =>
        {
            GD.Print("Disconnected from MQTT broker.");
            EmitSignal("BrokerDisconnected");
        };

        await mqttClient.ConnectAsync(options);
    }

    public async Task PublishNBIRTH()
    {
        var payload = new Payload
        {
            Timestamp = (ulong)(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()),
            Seq = sequenceNumber++
        };
        // Add metrics as needed
        var message = new MqttApplicationMessageBuilder()
            .WithTopic($"spBv1.0/{GroupId}/{NodeId}/NBIRTH")
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(true)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishNDEATH()
    {
        var payload = new Payload
        {
            Timestamp = (ulong)(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()),
            Seq = sequenceNumber++
        };
        var message = new MqttApplicationMessageBuilder()
            .WithTopic($"spBv1.0/{GroupId}/{NodeId}/NDEATH")
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(true)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishDBIRTH(string deviceId)
    {
        var payload = new Payload
        {
            Timestamp = (ulong)(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()),
            Seq = sequenceNumber++
        };
        var topic = $"spBv1.0/{GroupId}/{NodeId}/{DeviceIdPrefix}{deviceId}/DBIRTH";
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(true)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishDDEATH(string deviceId)
    {
        var payload = new Payload
        {
            Timestamp = (ulong)(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()),
            Seq = sequenceNumber++
        };
        var topic = $"spBv1.0/{GroupId}/{NodeId}/{DeviceIdPrefix}{deviceId}/DDEATH";
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(true)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishNDATA(Payload payload)
    {
        payload.Seq = sequenceNumber++;
        var topic = $"spBv1.0/{GroupId}/{NodeId}/NDATA";
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(false)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishDDATA(string deviceId, Payload payload)
    {
        payload.Seq = sequenceNumber++;
        var topic = $"spBv1.0/{GroupId}/{NodeId}/{DeviceIdPrefix}{deviceId}/DDATA";
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(false)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishNCMD(Payload payload)
    {
        payload.Seq = sequenceNumber++;
        var topic = $"spBv1.0/{GroupId}/{NodeId}/NCMD";
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(false)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        await mqttClient.PublishAsync(message);
    }

    public async Task PublishDCMD(string deviceId, Payload payload)
    {
        payload.Seq = sequenceNumber++;
        var topic = $"spBv1.0/{GroupId}/{NodeId}/{DeviceIdPrefix}{deviceId}/DCMD";
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload.ToByteArray())
            .WithRetainFlag(false)
            .WithQualityOfServiceLevel(MQTTnet.Protocol.MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

    }
}

